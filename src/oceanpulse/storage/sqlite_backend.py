"""SQLite backend: thread-safe, WAL, source-aware upserts.

Two things here are load-bearing and easy to get wrong.

**Concurrency.** The ingestion daemon writes while Dash callbacks read. Under
SQLite's default rollback journal that produces `database is locked` under
ordinary use. WAL mode lets readers and the writer proceed simultaneously, and
it must be set on *every* connection, not once at creation. Connections are
thread-local because a sqlite3 connection is not safe to share across threads.

**Merging sources.** Observations are keyed on position and time alone, so an
Open-Meteo wave reading and an ERDDAP sea-level reading for the same cell and
hour land on the same row and must merge rather than overwrite. But a plain
`COALESCE(old, new)` is also wrong: it would freeze the first forecast value
for an hour forever and never accept the corrected one. So each source
declares which columns it *owns* (overwrite) and which it may only *fill*
(write when NULL). See `models.SOURCE_FIELDS`.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..logging_setup import get_logger
from ..math_engine import haversine_km
from ..models import (
    FILL_ONLY_FIELDS,
    MEASUREMENT_FIELDS,
    SOURCE_FIELDS,
    MarineObservation,
)
from .base import BoundingBox, ObservationFilter

log = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema_sqlite.sql")

OBSERVATION_COLUMNS = (
    "obs_id",
    "latitude",
    "longitude",
    "timestamp",
    "port_id",
    *MEASUREMENT_FIELDS,
    "is_forecast",
    "is_historical_cache",
    "sources",
    "created_at",
    "updated_at",
)


def now_ms() -> int:
    return int(time.time() * 1000)


class SQLiteStorage:
    """Thread-safe SQLite storage for observations, grid state and settings."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialised = False

    # -- connection management -------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path), timeout=30.0, isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        # WAL on every connection, not just the one that created the file.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @property
    def conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._connect()
            self._local.connection = connection
        return connection

    def initialise(self) -> None:
        if self._initialised:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            self.conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._initialised = True
        log.info("Database ready at %s", self.db_path)

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            connection.close()
            self._local.connection = None

    # -- observations -----------------------------------------------------

    def _build_upsert(self, source: str) -> str:
        owned = set(SOURCE_FIELDS.get(source, MEASUREMENT_FIELDS))
        fill_only = set(FILL_ONLY_FIELDS.get(source, ()))

        assignments: list[str] = []
        for column in MEASUREMENT_FIELDS:
            if column in fill_only:
                # Only populate a gap; never displace a better measurement.
                assignments.append(
                    f"{column} = COALESCE(marine_observations.{column}, excluded.{column})"
                )
            elif column in owned:
                # The owning source may correct its own earlier value, but a
                # NULL from it must not erase what another source supplied.
                assignments.append(
                    f"{column} = COALESCE(excluded.{column}, marine_observations.{column})"
                )

        assignments.extend(
            [
                "port_id = COALESCE(excluded.port_id, marine_observations.port_id)",
                # Both flags latch on and are never cleared by an incoming row.
                "is_historical_cache = MAX("
                "marine_observations.is_historical_cache, excluded.is_historical_cache)",
                # A forecast hour that has since happened is re-fetched as an
                # analysis, so this one does clear - but only downwards.
                "is_forecast = MIN(marine_observations.is_forecast, excluded.is_forecast)",
                "sources = CASE WHEN instr(',' || marine_observations.sources || ',', "
                "',' || excluded.sources || ',') > 0 THEN marine_observations.sources "
                "ELSE marine_observations.sources || ',' || excluded.sources END",
                "updated_at = excluded.updated_at",
            ]
        )

        placeholders = ", ".join("?" for _ in OBSERVATION_COLUMNS)
        return (
            f"INSERT INTO marine_observations ({', '.join(OBSERVATION_COLUMNS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(obs_id) DO UPDATE SET {', '.join(assignments)}"
        )

    def upsert_observations(
        self, observations: Sequence[MarineObservation], source: str = "open_meteo"
    ) -> int:
        if not observations:
            return 0
        stamp = now_ms()
        rows = []
        for obs in observations:
            rows.append(
                (
                    obs.obs_id,
                    obs.latitude,
                    obs.longitude,
                    obs.timestamp,
                    obs.port_id,
                    *[getattr(obs, name) for name in MEASUREMENT_FIELDS],
                    int(obs.is_forecast),
                    int(obs.is_historical_cache),
                    source,
                    stamp,
                    stamp,
                )
            )
        sql = self._build_upsert(source)
        with self._write_lock:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.executemany(sql, rows)
                cursor.execute("COMMIT")
            except sqlite3.Error:
                cursor.execute("ROLLBACK")
                raise
        return len(rows)

    def query_observations(self, filters: ObservationFilter) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if filters.start_ms is not None:
            clauses.append("timestamp >= ?")
            params.append(int(filters.start_ms))
        if filters.end_ms is not None:
            clauses.append("timestamp <= ?")
            params.append(int(filters.end_ms))
        if filters.port_id is not None:
            clauses.append("port_id = ?")
            params.append(filters.port_id)
        if not filters.include_forecast:
            clauses.append("is_forecast = 0")

        bbox = filters.bbox.normalised() if filters.bbox else None
        centre_box: BoundingBox | None = None
        if filters.centre and filters.radius_km:
            centre_box = _radius_bounding_box(
                filters.centre[0], filters.centre[1], filters.radius_km
            )

        for box in (bbox, centre_box):
            if box is None:
                continue
            clauses.append("latitude BETWEEN ? AND ?")
            params.extend([box.min_lat, box.max_lat])
            if box.min_lon <= box.max_lon:
                clauses.append("longitude BETWEEN ? AND ?")
                params.extend([box.min_lon, box.max_lon])
            else:
                # The box straddles the antimeridian, so it is two boxes.
                clauses.append("(longitude >= ? OR longitude <= ?)")
                params.extend([box.min_lon, box.max_lon])

        for column in filters.require_columns:
            if column in MEASUREMENT_FIELDS:
                clauses.append(f"{column} IS NOT NULL")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "DESC" if filters.order.lower() == "desc" else "ASC"
        # Over-fetch when a radius filter will discard corners of the box.
        sql_limit = ""
        if filters.limit is not None and not (filters.centre and filters.radius_km):
            sql_limit = f"LIMIT {int(filters.limit)}"

        sql = (
            f"SELECT * FROM marine_observations {where} "
            f"ORDER BY timestamp {order} {sql_limit}"
        )
        rows = [dict(row) for row in self.conn.execute(sql, params)]

        if filters.centre and filters.radius_km:
            lat0, lon0 = filters.centre
            radius = float(filters.radius_km)
            kept = []
            for row in rows:
                distance = haversine_km(lat0, lon0, row["latitude"], row["longitude"])
                if distance <= radius:
                    row["distance_km"] = distance
                    kept.append(row)
            rows = kept
            if filters.limit is not None:
                rows = rows[: int(filters.limit)]
        return rows

    def latest_observation_time(self, port_id: str | None = None) -> int | None:
        if port_id is None:
            sql = "SELECT MAX(timestamp) AS t FROM marine_observations WHERE is_forecast = 0"
            row = self.conn.execute(sql).fetchone()
        else:
            sql = (
                "SELECT MAX(timestamp) AS t FROM marine_observations "
                "WHERE port_id = ? AND is_forecast = 0"
            )
            row = self.conn.execute(sql, (port_id,)).fetchone()
        return int(row["t"]) if row and row["t"] is not None else None

    # -- grid points ------------------------------------------------------

    def upsert_grid_points(self, points: Iterable[tuple[float, float, bool]]) -> int:
        rows = [
            (f"{lat:.4f}|{lon:.4f}", float(lat), float(lon), int(bool(is_ocean)))
            for lat, lon, is_ocean in points
        ]
        if not rows:
            return 0
        with self._write_lock:
            self.conn.executemany(
                "INSERT INTO grid_points (grid_id, latitude, longitude, is_ocean) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(grid_id) DO UPDATE SET "
                "is_ocean = excluded.is_ocean",
                rows,
            )
        return len(rows)

    def get_grid_points(self, only_valid: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM grid_points WHERE is_ocean = 1"
        if only_valid:
            # NULL means "not probed yet" and is worth trying once.
            sql += " AND (is_valid IS NULL OR is_valid = 1)"
        return [dict(row) for row in self.conn.execute(sql + " ORDER BY latitude, longitude")]

    def mark_grid_probe(self, grid_ids_valid: Sequence[str], grid_ids_invalid: Sequence[str]) -> None:
        stamp = now_ms()
        with self._write_lock:
            if grid_ids_valid:
                self.conn.executemany(
                    "UPDATE grid_points SET is_valid = 1, fail_count = 0, "
                    "last_checked = ?, last_success = ? WHERE grid_id = ?",
                    [(stamp, stamp, gid) for gid in grid_ids_valid],
                )
            if grid_ids_invalid:
                self.conn.executemany(
                    "UPDATE grid_points SET is_valid = 0, fail_count = fail_count + 1, "
                    "last_checked = ? WHERE grid_id = ?",
                    [(stamp, gid) for gid in grid_ids_invalid],
                )

    def clear_grid(self) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM grid_points")

    # -- tracked ports ----------------------------------------------------

    def add_tracked_port(self, port: dict[str, Any]) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO tracked_ports (port_id, port_name, country_code, country_name, "
                "latitude, longitude, marine_latitude, marine_longitude, elevation_m, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(port_id) DO UPDATE SET "
                "port_name = excluded.port_name, "
                "marine_latitude = COALESCE(excluded.marine_latitude, "
                "tracked_ports.marine_latitude), "
                "marine_longitude = COALESCE(excluded.marine_longitude, "
                "tracked_ports.marine_longitude), "
                "elevation_m = COALESCE(excluded.elevation_m, tracked_ports.elevation_m)",
                (
                    port["port_id"],
                    port["port_name"],
                    port.get("country_code", ""),
                    port.get("country_name", ""),
                    float(port["latitude"]),
                    float(port["longitude"]),
                    port.get("marine_latitude"),
                    port.get("marine_longitude"),
                    port.get("elevation_m"),
                    now_ms(),
                ),
            )

    def get_tracked_ports(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute("SELECT * FROM tracked_ports ORDER BY port_name")
        ]

    def get_tracked_port(self, port_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM tracked_ports WHERE port_id = ?", (port_id,)
        ).fetchone()
        return dict(row) if row else None

    def remove_tracked_port(self, port_id: str) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM tracked_ports WHERE port_id = ?", (port_id,))

    def mark_port_polled(self, port_id: str, backfilled_to: int | None = None) -> None:
        with self._write_lock:
            if backfilled_to is None:
                self.conn.execute(
                    "UPDATE tracked_ports SET last_polled_at = ? WHERE port_id = ?",
                    (now_ms(), port_id),
                )
            else:
                self.conn.execute(
                    "UPDATE tracked_ports SET last_polled_at = ?, backfilled_to = ? "
                    "WHERE port_id = ?",
                    (now_ms(), int(backfilled_to), port_id),
                )

    # -- cache ledger -----------------------------------------------------

    def record_fetch(
        self,
        dataset: str,
        bbox: BoundingBox,
        start_ms: int,
        end_ms: int,
        row_count: int = 0,
    ) -> None:
        box = bbox.normalised()
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO cache_ledger (dataset, min_lat, max_lat, min_lon, max_lon, "
                "start_timestamp, end_timestamp, row_count, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset,
                    box.min_lat,
                    box.max_lat,
                    box.min_lon,
                    box.max_lon,
                    int(start_ms),
                    int(end_ms),
                    int(row_count),
                    now_ms(),
                ),
            )

    def is_range_cached(
        self, dataset: str, bbox: BoundingBox, start_ms: int, end_ms: int
    ) -> bool:
        """True only when one earlier fetch *fully contains* this request.

        Containment, not overlap. The intuitive "is there something nearby"
        test reports a hit when a tiny cached box sits inside a large request,
        and the user is then handed a fraction of the data believing it whole.
        """
        box = bbox.normalised()
        row = self.conn.execute(
            "SELECT 1 FROM cache_ledger WHERE dataset = ? "
            "AND min_lat <= ? AND max_lat >= ? AND min_lon <= ? AND max_lon >= ? "
            "AND start_timestamp <= ? AND end_timestamp >= ? LIMIT 1",
            (
                dataset,
                box.min_lat,
                box.max_lat,
                box.min_lon,
                box.max_lon,
                int(start_ms),
                int(end_ms),
            ),
        ).fetchone()
        return row is not None

    # -- saved datasets ---------------------------------------------------

    def save_dataset(self, name: str, spec: dict[str, Any], notes: str = "") -> int:
        payload = json.dumps(spec, sort_keys=True)
        with self._write_lock:
            cursor = self.conn.execute(
                "INSERT INTO saved_datasets (name, spec_json, notes, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "spec_json = excluded.spec_json, notes = excluded.notes",
                (name.strip(), payload, notes, now_ms()),
            )
        return int(cursor.lastrowid or 0)

    def list_saved_datasets(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM saved_datasets ORDER BY COALESCE(last_run_at, created_at) DESC"
        )
        out = []
        for row in rows:
            record = dict(row)
            try:
                record["spec"] = json.loads(record["spec_json"])
            except (TypeError, ValueError):
                record["spec"] = {}
            out.append(record)
        return out

    def get_saved_dataset(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM saved_datasets WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        try:
            record["spec"] = json.loads(record["spec_json"])
        except (TypeError, ValueError):
            record["spec"] = {}
        return record

    def delete_saved_dataset(self, name: str) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM saved_datasets WHERE name = ?", (name,))

    def mark_dataset_run(self, name: str, rows: int) -> None:
        with self._write_lock:
            self.conn.execute(
                "UPDATE saved_datasets SET last_run_at = ?, last_rows = ? WHERE name = ?",
                (now_ms(), int(rows), name),
            )

    # -- settings and heartbeat -------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, str(value), now_ms()),
            )

    def write_heartbeat(self, status: str, detail: str = "") -> None:
        self.set_setting("daemon_status", status)
        self.set_setting("daemon_heartbeat", str(now_ms()))
        if detail:
            self.set_setting("daemon_detail", detail[:500])

    def daemon_health(self, stale_after_seconds: int = 300) -> dict[str, Any]:
        """Report ACTIVE / DEGRADED / STOPPED from the heartbeat."""
        heartbeat = self.get_setting("daemon_heartbeat")
        status = self.get_setting("daemon_status", "stopped") or "stopped"
        detail = self.get_setting("daemon_detail", "") or ""
        if heartbeat is None:
            return {"status": "stopped", "age_seconds": None, "detail": detail}
        age = max(0.0, (now_ms() - int(heartbeat)) / 1000.0)
        if age > stale_after_seconds:
            resolved = "stopped"
        elif status == "degraded":
            resolved = "degraded"
        else:
            resolved = "active"
        return {"status": resolved, "age_seconds": age, "detail": detail}

    # -- statistics --------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN is_forecast = 0 THEN 1 ELSE 0 END) AS analyses, "
            "MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
            "FROM marine_observations"
        ).fetchone()
        size_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                size_bytes += candidate.stat().st_size
        ports = self.conn.execute("SELECT COUNT(*) AS n FROM tracked_ports").fetchone()
        grid = self.conn.execute(
            "SELECT COUNT(*) AS n FROM grid_points WHERE is_valid = 1"
        ).fetchone()
        return {
            "total_observations": int(row["total"] or 0),
            "analysis_observations": int(row["analyses"] or 0),
            "first_timestamp": row["first_ts"],
            "last_timestamp": row["last_ts"],
            "database_bytes": size_bytes,
            "tracked_ports": int(ports["n"] or 0),
            "valid_grid_points": int(grid["n"] or 0),
        }


def _radius_bounding_box(lat: float, lon: float, radius_km: float) -> BoundingBox:
    """Bounding box enclosing a radius, for use as a cheap index prefilter.

    Near the poles the longitude span blows up, so it is clamped to the whole
    world rather than allowed to produce nonsense.
    """
    lat_delta = radius_km / 111.32
    cos_lat = math.cos(math.radians(lat))
    if abs(cos_lat) < 1e-6 or radius_km / (111.32 * abs(cos_lat)) >= 180.0:
        return BoundingBox(
            min_lat=max(-90.0, lat - lat_delta),
            max_lat=min(90.0, lat + lat_delta),
            min_lon=-180.0,
            max_lon=180.0,
        )
    lon_delta = radius_km / (111.32 * abs(cos_lat))
    min_lon = lon - lon_delta
    max_lon = lon + lon_delta
    # Leave a wrapped box wrapped; the query layer turns it into an OR.
    if min_lon < -180.0:
        min_lon += 360.0
    if max_lon > 180.0:
        max_lon -= 360.0
    return BoundingBox(
        min_lat=max(-90.0, lat - lat_delta),
        max_lat=min(90.0, lat + lat_delta),
        min_lon=min_lon,
        max_lon=max_lon,
    )
