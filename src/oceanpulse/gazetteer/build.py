"""Build the offline gazetteer.

Two sources, blended:

* **NGA World Port Index** - 2,951 real ports with harbour metadata. The
  authoritative marine gazetteer, and every entry is by definition on water.
  Its coordinates are **DMS strings** (`30°20'00"N`), not decimal degrees;
  reading them as floats yields 30.0 for everything and silently piles every
  port in a country onto the same spot.

* **GeoNames** coastal cities - the World Port Index alone is thin for
  search-as-you-type, and users type "Mumbai", not "Jawaharlal Nehru". Cities
  are filtered against the ocean mask so the gazetteer for an ocean
  application does not offer Ulaanbaatar.

The FTS5 index is **contentless** (`content=''`), which stores the inverted
index without a second copy of the text. That means it cannot return column
values - a contentless table hands back rowids and nothing else. Columns are
read from a companion base table joined on rowid. Declaring latitude and
longitude inside a contentless FTS5 table, `UNINDEXED` or not, yields NULL on
every read.

The database is written to a temporary file and swapped into place on success,
so an interrupted build never leaves a half-populated index behind.
"""

from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import GEONAMES_BASE_URL, WPI_URL
from ..logging_setup import get_logger
from ..math_engine import haversine_km
from ..models import PortRecord
from ..ingest.grid import OceanMask
from ..ingest.http import PermanentError, RateLimitedClient, TransientError

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ports (
    rowid            INTEGER PRIMARY KEY,
    port_id          TEXT UNIQUE NOT NULL,
    port_name        TEXT NOT NULL,
    name_folded      TEXT NOT NULL,
    country_code     TEXT NOT NULL DEFAULT '',
    country_name     TEXT NOT NULL DEFAULT '',
    latitude         REAL NOT NULL,
    longitude        REAL NOT NULL,
    water_body       TEXT NOT NULL DEFAULT '',
    harbor_size      TEXT NOT NULL DEFAULT '',
    harbor_type      TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT 'wpi',
    population       INTEGER NOT NULL DEFAULT 0,
    marine_latitude  REAL,
    marine_longitude REAL
);

CREATE INDEX IF NOT EXISTS idx_ports_folded ON ports(name_folded);
CREATE INDEX IF NOT EXISTS idx_ports_population ON ports(population DESC);

-- Contentless: the index only. Values come from `ports` via rowid.
CREATE VIRTUAL TABLE IF NOT EXISTS ports_fts USING fts5(
    port_name,
    alt_names,
    country_name,
    water_body,
    content='',
    prefix='2 3 4'
);

CREATE TABLE IF NOT EXISTS gazetteer_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 30°20'00"N and friends. The degree sign may be absent, and some rows use a
# plain apostrophe or a typographic one for minutes.
_DMS_PATTERN = re.compile(
    r"^\s*(?P<deg>\d+(?:\.\d+)?)\s*[°°d]?\s*"
    r"(?:(?P<min>\d+(?:\.\d+)?)\s*['’ʹ]?\s*)?"
    r"(?:(?P<sec>\d+(?:\.\d+)?)\s*[\"”ʺ]?\s*)?"
    r"(?P<hemi>[NSEW])\s*$",
    re.IGNORECASE,
)


def parse_dms(value: str) -> float | None:
    """Convert a World Port Index coordinate to decimal degrees.

    Accepts `30°20'00"N`, `30 20 00 N`, `30.5N`, and returns None for anything
    it cannot interpret rather than guessing.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    match = _DMS_PATTERN.match(text)
    if not match:
        # Some rows are already decimal, optionally signed.
        try:
            return float(text)
        except ValueError:
            return None

    degrees = float(match.group("deg"))
    minutes = float(match.group("min") or 0.0)
    seconds = float(match.group("sec") or 0.0)
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if match.group("hemi").upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def fold(text: str) -> str:
    """Strip diacritics and case so `Nawada` matches `Nawāda`."""
    normalised = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in normalised if not unicodedata.combining(ch))
    return stripped.casefold().strip()


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------


def parse_wpi(csv_text: str) -> list[PortRecord]:
    """Parse the World Port Index CSV into port records."""
    reader = csv.DictReader(io.StringIO(csv_text))
    records: list[PortRecord] = []
    skipped = 0
    for row in reader:
        latitude = parse_dms(row.get("latitude", ""))
        longitude = parse_dms(row.get("longitude", ""))
        name = (row.get("portName") or "").strip()
        if latitude is None or longitude is None or not name:
            skipped += 1
            continue
        port_number = (row.get("portNumber") or "").strip() or name
        try:
            records.append(
                PortRecord(
                    port_id=f"wpi:{port_number}",
                    port_name=name,
                    country_code=(row.get("countryCode") or "").strip().upper(),
                    country_name=(row.get("countryName") or "").strip(),
                    latitude=latitude,
                    longitude=longitude,
                    water_body=(row.get("regionName") or "").strip().title(),
                    harbor_size=(row.get("harborSize") or "").strip(),
                    harbor_type=(row.get("harborType") or "").strip(),
                    source="wpi",
                )
            )
        except ValueError:
            skipped += 1
    if skipped:
        log.warning("World Port Index: skipped %d unparseable rows", skipped)
    log.info("World Port Index: %d ports parsed", len(records))
    return records


def parse_geonames(
    text: str, mask: OceanMask | None, coastal_max_km: float = 60.0
) -> list[PortRecord]:
    """Parse GeoNames city rows, keeping only places near the sea."""
    records: list[PortRecord] = []
    inland = 0
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 15:
            continue
        try:
            geoname_id = fields[0]
            name = fields[1].strip()
            latitude = float(fields[4])
            longitude = float(fields[5])
            country_code = fields[8].strip().upper()
            population = int(fields[14] or 0)
        except (ValueError, IndexError):
            continue
        if not name:
            continue
        if mask is not None and not mask.is_empty:
            if not mask.is_coastal(latitude, longitude, max_km=coastal_max_km):
                inland += 1
                continue
        records.append(
            PortRecord(
                port_id=f"geonames:{geoname_id}",
                port_name=name,
                country_code=country_code,
                latitude=latitude,
                longitude=longitude,
                source="geonames",
                population=population,
            )
        )
    log.info("GeoNames: %d coastal places kept, %d inland discarded", len(records), inland)
    return records


def _alt_names(line_fields: Sequence[str]) -> str:
    try:
        return line_fields[3][:600]
    except IndexError:
        return ""


# Ports and their cities are rarely more than a few tens of km apart.
PORT_CITY_MATCH_KM = 35.0


def attach_city_population(
    ports: Sequence[PortRecord], cities: Sequence[PortRecord]
) -> int:
    """Give each port the population of the settlement it serves.

    World Port Index rows carry no population, so without this every port ties
    with every other and the ranking falls back to whatever order the database
    happens to return. That is how a search for `sydn` surfaces Sydney, Nova
    Scotia (population 30,000) above Sydney, Australia - exactly the failure
    mode that makes a search box feel broken.

    Cities are bucketed into one-degree cells so this stays a local lookup
    rather than 3,000 x 17,000 distance calculations.
    """
    buckets: dict[tuple[int, int], list[PortRecord]] = {}
    for city in cities:
        key = (int(math.floor(city.latitude)), int(math.floor(city.longitude)))
        buckets.setdefault(key, []).append(city)

    enriched = 0
    for port in ports:
        base_lat = int(math.floor(port.latitude))
        base_lon = int(math.floor(port.longitude))
        best: tuple[int, float] | None = None
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for city in buckets.get((base_lat + dlat, base_lon + dlon), ()):
                    distance = haversine_km(
                        port.latitude, port.longitude, city.latitude, city.longitude
                    )
                    if distance > PORT_CITY_MATCH_KM:
                        continue
                    # Prefer the largest settlement in range, not merely the
                    # closest: a port's identity comes from the city it serves.
                    if best is None or city.population > best[0]:
                        best = (city.population, distance)
        if best is not None and best[0] > 0:
            port.population = best[0]
            enriched += 1
    return enriched


def parse_country_names(text: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) > 4 and fields[0].strip():
            names[fields[0].strip().upper()] = fields[4].strip()
    return names


# ---------------------------------------------------------------------------
# Database writing
# ---------------------------------------------------------------------------


def write_gazetteer(
    path: Path,
    records: Iterable[PortRecord],
    alt_names: dict[str, str] | None = None,
) -> int:
    """Write the gazetteer to `path`, atomically.

    The FTS index is populated with an explicit INSERT. An FTS5 table is never
    filled automatically: declaring one and writing only to the base table
    produces an index that matches nothing at all, raises no error anywhere,
    and looks exactly like a search box that simply never finds anything.
    """
    alt_names = alt_names or {}
    temporary = path.with_suffix(path.suffix + ".building")
    if temporary.exists():
        temporary.unlink()
    temporary.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(temporary))
    try:
        connection.executescript("PRAGMA journal_mode=WAL;\n" + SCHEMA)
        cursor = connection.cursor()
        count = 0
        for record in records:
            cursor.execute(
                "INSERT OR IGNORE INTO ports (port_id, port_name, name_folded, country_code, "
                "country_name, latitude, longitude, water_body, harbor_size, harbor_type, "
                "source, population, marine_latitude, marine_longitude) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.port_id,
                    record.port_name,
                    fold(record.port_name),
                    record.country_code,
                    record.country_name,
                    record.latitude,
                    record.longitude,
                    record.water_body,
                    record.harbor_size,
                    record.harbor_type,
                    record.source,
                    record.population,
                    record.marine_latitude,
                    record.marine_longitude,
                ),
            )
            if cursor.rowcount == 0:
                continue
            rowid = cursor.lastrowid
            # Index the folded form too, so an accented place is reachable
            # from an unaccented query.
            folded = fold(record.port_name)
            searchable_name = record.port_name
            if folded != record.port_name.casefold():
                searchable_name = f"{record.port_name} {folded}"
            cursor.execute(
                "INSERT INTO ports_fts (rowid, port_name, alt_names, country_name, water_body) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    rowid,
                    searchable_name,
                    fold(alt_names.get(record.port_id, "")),
                    record.country_name,
                    record.water_body,
                ),
            )
            count += 1

        cursor.execute(
            "INSERT OR REPLACE INTO gazetteer_meta (key, value) VALUES ('entries', ?)",
            (str(count),),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    for suffix in ("-wal", "-shm"):
        stray = Path(str(temporary) + suffix)
        if stray.exists():
            stray.unlink()
    temporary.replace(path)
    log.info("Gazetteer written to %s (%d entries)", path, count)
    return count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def build_gazetteer(
    client: RateLimitedClient,
    output_path: Path,
    *,
    mask: OceanMask | None = None,
    geonames_dataset: str = "cities15000",
    include_cities: bool = True,
    coastal_max_km: float = 60.0,
) -> dict[str, Any]:
    """Download the sources and build the gazetteer.

    Partial success is fine and expected: if the World Port Index is
    unreachable the coastal cities still make a usable search index, and vice
    versa. Only a total failure raises.
    """
    report: dict[str, Any] = {"wpi": 0, "geonames": 0, "entries": 0, "errors": []}
    records: list[PortRecord] = []
    wpi_records: list[PortRecord] = []
    city_records: list[PortRecord] = []
    alt_names: dict[str, str] = {}

    # -- World Port Index --------------------------------------------------
    try:
        # The bare endpoint answers 400; the output format is mandatory.
        wpi_text = await client.get_text(WPI_URL)
        wpi_records = parse_wpi(wpi_text)
        records.extend(wpi_records)
        report["wpi"] = len(wpi_records)
    except (TransientError, PermanentError) as exc:
        log.warning("World Port Index unavailable: %s", exc)
        report["errors"].append(f"world port index: {exc}")

    # -- GeoNames coastal cities -------------------------------------------
    if include_cities:
        try:
            country_text = await client.get_text(f"{GEONAMES_BASE_URL}/countryInfo.txt")
            country_names = parse_country_names(country_text)
        except (TransientError, PermanentError) as exc:
            log.warning("GeoNames country list unavailable: %s", exc)
            country_names = {}

        try:
            archive = await client.get_bytes(
                f"{GEONAMES_BASE_URL}/{geonames_dataset}.zip"
            )
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                member = f"{geonames_dataset}.txt"
                raw = bundle.read(member).decode("utf-8", errors="replace")
            city_records = parse_geonames(raw, mask, coastal_max_km=coastal_max_km)
            for record in city_records:
                record.country_name = country_names.get(record.country_code, "")
            # WPI supplies a country name already; GeoNames only a code.
            for record in wpi_records:
                if not record.country_name:
                    record.country_name = country_names.get(record.country_code, "")
            # Alternate names make "Bombay" find Mumbai.
            for line in raw.splitlines():
                fields = line.split("\t")
                if len(fields) > 3 and fields[0]:
                    alt_names[f"geonames:{fields[0]}"] = _alt_names(fields)
            records.extend(city_records)
            report["geonames"] = len(city_records)
        except (TransientError, PermanentError, zipfile.BadZipFile, KeyError) as exc:
            log.warning("GeoNames cities unavailable: %s", exc)
            report["errors"].append(f"geonames: {exc}")

    if not records:
        raise RuntimeError(
            "gazetteer build failed: neither the World Port Index nor GeoNames "
            "could be retrieved"
        )

    if wpi_records and city_records:
        enriched = attach_city_population(wpi_records, city_records)
        log.info("population attached to %d of %d ports", enriched, len(wpi_records))
        report["ports_with_population"] = enriched

    # Ports first, so a port and a city with the same name resolve to the port.
    records.sort(key=lambda r: (r.source != "wpi", -r.population, r.port_name))
    report["entries"] = write_gazetteer(output_path, records, alt_names)
    return report


def gazetteer_exists(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 4096:
        return False
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = connection.execute("SELECT COUNT(*) FROM ports").fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.Error:
        return False
    finally:
        connection.close()
