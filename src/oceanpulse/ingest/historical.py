"""On-demand historical backfill for a tracked port.

Called from the interface when a user loads a timeline. Each source is
consulted only for the period it can actually serve, and each completed fetch
is written to the ledger so the same request is never paid for twice.

Coverage differs sharply by source, and pretending otherwise is what produces
empty charts:

    waves, currents   Open-Meteo marine   2021-12 onwards (per-variable)
    sea temperature   ERDDAP OISST v2.1   1981-09 onwards
    sea level         ERDDAP nesdisSSH    2017-02 onwards, months in arrears
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..config import Config
from ..logging_setup import get_logger
from ..models import MarineObservation
from ..storage.base import BoundingBox
from ..storage.sqlite_backend import SQLiteStorage
from .cache_ledger import date_to_ms, point_bbox
from .http import PermanentError, RateLimitedClient, TransientError
from .noaa_erddap import ErddapClient
from .open_meteo import OpenMeteoMarine, archive_floor

log = get_logger(__name__)


async def backfill_port(
    config: Config,
    storage: SQLiteStorage,
    client: RateLimitedClient,
    port: dict[str, Any],
    start: dt.date,
    end: dt.date,
    *,
    include_erddap: bool = True,
    erddap: ErddapClient | None = None,
) -> dict[str, Any]:
    """Fetch whatever this port is missing over `start`..`end`.

    Returns a per-source report so the interface can say what it actually did
    rather than showing a spinner and a blank chart.
    """
    latitude = float(port.get("marine_latitude") or port["latitude"])
    longitude = float(port.get("marine_longitude") or port["longitude"])
    port_id = str(port["port_id"])
    bbox = point_bbox(latitude, longitude)
    start_ms, end_ms = date_to_ms(start), date_to_ms(end, end_of_day=True)

    report: dict[str, Any] = {"port_id": port_id, "sources": {}, "stored": 0}

    # -- Open-Meteo marine: waves, currents, recent SST --------------------
    marine_floor = archive_floor()
    marine_start = max(start, marine_floor)
    dataset = f"open_meteo:{port_id}"
    if marine_start > end:
        report["sources"]["open_meteo"] = {
            "status": "out_of_range",
            "message": f"marine archive begins {marine_floor.isoformat()}",
        }
    elif storage.is_range_cached(dataset, bbox, date_to_ms(marine_start), end_ms):
        report["sources"]["open_meteo"] = {"status": "cached"}
    else:
        marine = OpenMeteoMarine(client)
        try:
            observations = await marine.fetch_range(
                latitude, longitude, marine_start, end, port_id=port_id
            )
        except (TransientError, PermanentError) as exc:
            log.warning("marine backfill failed for %s: %s", port_id, exc)
            report["sources"]["open_meteo"] = {"status": "error", "message": str(exc)[:200]}
        else:
            stored = _store(storage, observations, "open_meteo")
            storage.record_fetch(
                dataset, bbox, date_to_ms(marine_start), end_ms, row_count=stored
            )
            report["stored"] += stored
            report["sources"]["open_meteo"] = {
                "status": "fetched",
                "rows": stored,
                "from": marine_start.isoformat(),
            }

    if not include_erddap or not config.erddap_enabled:
        return report

    # Reuse the caller's client when given one. Building a fresh ErddapClient
    # per call threw away its resolved-cell and coverage caches, so every
    # timeline load re-probed the same masked coastline from scratch.
    if erddap is None:
        erddap = ErddapClient(client, store=storage)

    # -- ERDDAP: long-run sea temperature ---------------------------------
    await _backfill_erddap(
        storage,
        erddap,
        kind="sst",
        port_id=port_id,
        latitude=latitude,
        longitude=longitude,
        start=start,
        end=end,
        bbox=bbox,
        chunk_days=config.erddap_chunk_days,
        report=report,
    )

    # -- ERDDAP: sea level anomaly and geostrophic currents ---------------
    await _backfill_erddap(
        storage,
        erddap,
        kind="sla",
        port_id=port_id,
        latitude=latitude,
        longitude=longitude,
        start=start,
        end=end,
        bbox=bbox,
        chunk_days=config.erddap_chunk_days,
        report=report,
    )

    return report


async def _backfill_erddap(
    storage: SQLiteStorage,
    erddap: ErddapClient,
    *,
    kind: str,
    port_id: str,
    latitude: float,
    longitude: float,
    start: dt.date,
    end: dt.date,
    bbox: BoundingBox,
    chunk_days: int,
    report: dict[str, Any],
) -> None:
    dataset = f"erddap_{kind}:{port_id}"
    bounds = await erddap.time_range(kind)
    effective_start, effective_end = start, end
    if bounds is not None:
        effective_start = max(start, bounds[0].date())
        effective_end = min(end, bounds[1].date())
    if effective_start > effective_end:
        report["sources"][f"erddap_{kind}"] = {
            "status": "out_of_range",
            "message": (
                f"dataset covers {bounds[0].date()}..{bounds[1].date()}"
                if bounds
                else "coverage unknown"
            ),
        }
        return

    start_ms = date_to_ms(effective_start)
    end_ms = date_to_ms(effective_end, end_of_day=True)
    if storage.is_range_cached(dataset, bbox, start_ms, end_ms):
        report["sources"][f"erddap_{kind}"] = {"status": "cached"}
        return

    try:
        observations = await erddap.fetch_point_series(
            kind,
            latitude,
            longitude,
            effective_start,
            effective_end,
            port_id=port_id,
            chunk_days=chunk_days,
        )
    except (TransientError, PermanentError) as exc:
        log.warning("ERDDAP %s backfill failed for %s: %s", kind, port_id, exc)
        report["sources"][f"erddap_{kind}"] = {"status": "error", "message": str(exc)[:200]}
        return

    source = "erddap_sst" if kind.startswith("sst") else "erddap_sla"
    stored = _store(storage, observations, source)
    if stored:
        storage.record_fetch(dataset, bbox, start_ms, end_ms, row_count=stored)
    report["stored"] = report.get("stored", 0) + stored
    report["sources"][f"erddap_{kind}"] = {
        "status": "fetched",
        "rows": stored,
        "from": effective_start.isoformat(),
        "to": effective_end.isoformat(),
    }


def _store(
    storage: SQLiteStorage, observations: list[MarineObservation], source: str
) -> int:
    if not observations:
        return 0
    return storage.upsert_observations(observations, source=source)
