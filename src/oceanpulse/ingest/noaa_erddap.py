"""NOAA ERDDAP client for the deep historical archive.

ERDDAP is free, keyless and public domain, and it is also the least reliable
dependency in the project. Three separate failure shapes were observed while
building this, all within an hour:

* HTTP 503 "There was a (temporary?) problem" on a request that had just
  succeeded.
* HTTP 404 "Currently unknown datasetID=..." for a dataset that worked minutes
  earlier - ERDDAP reloading, not a missing dataset.
* One longitude variant of a dataset offline while its twin served fine.

So every dataset is declared as a *list* of equivalent variants across
longitude conventions and mirror hosts, and the client walks that list. A
single hardcoded endpoint, as originally specified, would fail regularly.

Dimension signatures also differ between datasets and cannot be assumed:
OISST is indexed `[time][zlev][latitude][longitude]`, while the sea-level
product is `[time][latitude][longitude]` with no depth axis at all.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import ERDDAP_HOSTS
from ..logging_setup import get_logger
from ..models import MarineObservation
from .http import PermanentError, RateLimitedClient, TransientError

log = get_logger(__name__)


@dataclass(frozen=True)
class ErddapDataset:
    """One concrete griddap dataset on one longitude convention."""

    dataset_id: str
    variables: tuple[str, ...]
    # Dimension order excluding time; used to build the subset expression.
    extra_dims: tuple[str, ...] = ()
    fixed_dims: dict[str, str] = field(default_factory=dict)
    lon_convention: str = "pm180"  # "pm180" or "0360"

    def encode_longitude(self, lon: float) -> float:
        if self.lon_convention == "0360":
            return lon % 360.0
        return ((lon + 180.0) % 360.0) - 180.0


# Column names in the returned CSV, mapped to model fields.
VARIABLE_MAP = {
    "sst": "sst_celsius",
    "sla": "sea_level_anomaly_m",
    "ugos": "geostrophic_u_ms",
    "vgos": "geostrophic_v_ms",
}

# Which model source each dataset kind writes as. This decides upsert
# precedence: ERDDAP daily SST may only fill gaps left by hourly Open-Meteo.
KIND_SOURCE = {"sst": "erddap_sst", "sst_nrt": "erddap_sst", "sla": "erddap_sla"}

_OISST_DIMS = ("zlev", "latitude", "longitude")
_OISST_FIXED = {"zlev": "0.0"}

DATASETS: dict[str, tuple[ErddapDataset, ...]] = {
    # Optimum Interpolation SST v2.1, daily 0.25 degree, 1981-09 onwards.
    "sst": (
        ErddapDataset("ncdcOisst21Agg_LonPM180", ("sst",), _OISST_DIMS, _OISST_FIXED, "pm180"),
        ErddapDataset("ncdcOisst21Agg", ("sst",), _OISST_DIMS, _OISST_FIXED, "0360"),
    ),
    # Near-real-time OISST, covering the weeks the final product lags behind.
    "sst_nrt": (
        ErddapDataset(
            "ncdcOisst21NrtAgg_LonPM180", ("sst",), _OISST_DIMS, _OISST_FIXED, "pm180"
        ),
        ErddapDataset("ncdcOisst21NrtAgg", ("sst",), _OISST_DIMS, _OISST_FIXED, "0360"),
    ),
    # Altimetric sea level anomaly and geostrophic currents, 2017 onwards.
    # Note this product runs months behind real time.
    "sla": (
        ErddapDataset(
            "nesdisSSH1day", ("sla", "ugos", "vgos"), ("latitude", "longitude"), {}, "pm180"
        ),
        ErddapDataset(
            "nesdisSSH1day_Lon0360",
            ("sla", "ugos", "vgos"),
            ("latitude", "longitude"),
            {},
            "0360",
        ),
    ),
}


def _subset(value: float | str) -> str:
    return f"[({value}):1:({value})]"


def _range_subset(start: str, end: str, stride: int = 1) -> str:
    return f"[({start}):{stride}:({end})]"


class ErddapClient:
    """Point and grid extraction with mirror and convention failover."""

    def __init__(self, client: RateLimitedClient, hosts: Sequence[str] = ERDDAP_HOSTS) -> None:
        self.client = client
        self.hosts = tuple(hosts)
        self._time_ranges: dict[str, tuple[dt.datetime, dt.datetime]] = {}
        # (kind, lat, lon) -> the cell that actually holds data, or None.
        self._resolved_cells: dict[
            tuple[str, float, float], tuple[float, float] | None
        ] = {}

    # -- metadata ---------------------------------------------------------

    async def time_range(self, kind: str) -> tuple[dt.datetime, dt.datetime] | None:
        """Actual temporal coverage of a dataset kind.

        Worth one request: the sea-level product lags real time by months, and
        asking for last week returns an error rather than an empty result.
        """
        if kind in self._time_ranges:
            return self._time_ranges[kind]

        for dataset in DATASETS.get(kind, ()):
            for host in self.hosts:
                url = f"{host}/info/{dataset.dataset_id}/index.csv"
                try:
                    text = await self.client.get_text(url, max_attempts=2)
                except (TransientError, PermanentError) as exc:
                    log.debug("info lookup failed for %s: %s", dataset.dataset_id, exc)
                    continue
                bounds = _parse_time_actual_range(text)
                if bounds:
                    self._time_ranges[kind] = bounds
                    log.info(
                        "%s coverage: %s .. %s",
                        kind,
                        bounds[0].date(),
                        bounds[1].date(),
                    )
                    return bounds
        log.warning("could not determine coverage for ERDDAP dataset kind %r", kind)
        return None

    # -- point series -----------------------------------------------------

    async def resolve_cell(
        self,
        kind: str,
        latitude: float,
        longitude: float,
        probe_end: dt.date,
    ) -> tuple[float, float] | None:
        """Find a nearby cell where this dataset actually holds values.

        A location that one product calls water, another calls land. Open-Meteo
        runs a wave model on its own grid and happily resolves a spot a few
        kilometres off Rotterdam; OISST, on a 0.25 degree mask, has that same
        cell masked as land and returns NaN for every day of it. Without this
        search, sea temperature and sea level come back empty for exactly the
        coastal ports the application exists to look at - and the failure looks
        identical to a broken fetch.

        Probes a ten-day window, nearest candidate first, and caches the answer.
        """
        cache_key = (kind, round(latitude, 3), round(longitude, 3))
        if cache_key in self._resolved_cells:
            return self._resolved_cells[cache_key]

        probe_start = probe_end - dt.timedelta(days=10)
        candidates: list[tuple[float, float]] = [(latitude, longitude)]
        for step in (0.25, 0.5, 1.0, 1.5):
            for dlat, dlon in (
                (0.0, -step), (0.0, step), (-step, 0.0), (step, 0.0),
                (-step, -step), (-step, step), (step, -step), (step, step),
            ):
                lat = max(-89.8, min(89.8, latitude + dlat))
                lon = ((longitude + dlon + 180.0) % 360.0) - 180.0
                candidates.append((round(lat, 4), round(lon, 4)))

        for lat, lon in candidates:
            rows = await self._fetch_chunk(kind, lat, lon, probe_start, probe_end)
            if _rows_to_observations(rows, port_id=None, source_kind=kind):
                if (lat, lon) != (latitude, longitude):
                    log.info(
                        "%s: %.3f,%.3f is masked; reading from %.3f,%.3f instead",
                        kind, latitude, longitude, lat, lon,
                    )
                self._resolved_cells[cache_key] = (lat, lon)
                return (lat, lon)

        log.info(
            "%s: no cell with data within 1.5 degrees of %.3f,%.3f",
            kind, latitude, longitude,
        )
        self._resolved_cells[cache_key] = None
        return None

    async def fetch_point_series(
        self,
        kind: str,
        latitude: float,
        longitude: float,
        start: dt.date,
        end: dt.date,
        *,
        port_id: str | None = None,
        chunk_days: int = 365,
        resolve: bool = True,
    ) -> list[MarineObservation]:
        """Daily series for one location, chunked to survive upstream timeouts."""
        bounds = await self.time_range(kind)
        if bounds is not None:
            start = max(start, bounds[0].date())
            end = min(end, bounds[1].date())
        if start > end:
            log.info("%s: requested window lies outside dataset coverage", kind)
            return []

        if resolve:
            resolved = await self.resolve_cell(kind, latitude, longitude, end)
            if resolved is None:
                return []
            latitude, longitude = resolved

        observations: list[MarineObservation] = []
        window_start = start
        while window_start <= end:
            window_end = min(end, window_start + dt.timedelta(days=chunk_days - 1))
            rows = await self._fetch_chunk(kind, latitude, longitude, window_start, window_end)
            observations.extend(
                _rows_to_observations(rows, port_id=port_id, source_kind=kind)
            )
            window_start = window_end + dt.timedelta(days=1)
        return observations

    def _point_query(
        self, dataset: ErddapDataset, latitude: float, longitude: float,
        start: dt.date, end: dt.date,
    ) -> str:
        lon = dataset.encode_longitude(longitude)
        dim_subset = ""
        for dim in dataset.extra_dims:
            if dim in dataset.fixed_dims:
                dim_subset += _subset(dataset.fixed_dims[dim])
            elif dim == "latitude":
                dim_subset += _subset(f"{latitude:.4f}")
            elif dim == "longitude":
                dim_subset += _subset(f"{lon:.4f}")
        time_subset = _range_subset(start.isoformat(), end.isoformat())
        return ",".join(
            f"{variable}{time_subset}{dim_subset}" for variable in dataset.variables
        )

    async def _fetch_chunk(
        self,
        kind: str,
        latitude: float,
        longitude: float,
        start: dt.date,
        end: dt.date,
    ) -> list[dict[str, Any]]:
        """Try every variant quickly, then retry patiently only if all failed.

        Two passes, because the two failure modes need opposite treatment. One
        dead variant among several working ones should cost seconds - so the
        first pass allows a single attempt each. A genuine upstream outage
        affects all of them, and only then is it worth backing off.
        """
        variants = [
            (dataset, host)
            for dataset in DATASETS.get(kind, ())
            for host in self.hosts
        ]
        last_error: Exception | None = None

        for attempts in (1, None):
            for dataset, host in variants:
                query = self._point_query(dataset, latitude, longitude, start, end)
                url = f"{host}/griddap/{dataset.dataset_id}.csv?{query}"
                try:
                    text = await self.client.get_text(url, max_attempts=attempts)
                except (TransientError, PermanentError) as exc:
                    last_error = exc
                    log.debug("ERDDAP %s on %s failed: %s", dataset.dataset_id, host, exc)
                    continue
                rows = _parse_griddap_csv(text)
                if rows:
                    return rows
            if attempts == 1:
                log.info(
                    "no ERDDAP variant for %s answered on the first pass; retrying with backoff",
                    kind,
                )

        if last_error is not None:
            log.warning(
                "all ERDDAP variants for %s failed over %s..%s: %s",
                kind,
                start,
                end,
                last_error,
            )
        return []

    # -- global grid ------------------------------------------------------

    async def fetch_global_sst_grid(
        self, on_date: dt.date, stride: int = 4
    ) -> list[tuple[float, float, float]]:
        """One global SST slice, used to derive the land/sea mask.

        `stride=4` on a 0.25 degree grid gives 1 degree cells: 64,800 values,
        about 3 MB of CSV, roughly twenty seconds. It is fetched once.
        """
        for dataset in DATASETS["sst"]:
            if dataset.lon_convention == "pm180":
                lat_span, lon_span = ("-89.875", "89.875"), ("-179.875", "179.875")
            else:
                lat_span, lon_span = ("-89.875", "89.875"), ("0.125", "359.875")

            query = (
                f"sst{_range_subset(on_date.isoformat(), on_date.isoformat())}"
                f"{_subset('0.0')}"
                f"{_range_subset(lat_span[0], lat_span[1], stride)}"
                f"{_range_subset(lon_span[0], lon_span[1], stride)}"
            )
            for host in self.hosts:
                url = f"{host}/griddap/{dataset.dataset_id}.csv?{query}"
                try:
                    # One attempt per variant: the mask is optional, and the
                    # grid falls back to probing if it never arrives.
                    text = await self.client.get_text(url, max_attempts=1)
                except (TransientError, PermanentError) as exc:
                    log.debug("mask fetch failed on %s: %s", host, exc)
                    continue
                rows = _parse_griddap_csv(text)
                if not rows:
                    continue
                out: list[tuple[float, float, float]] = []
                for row in rows:
                    try:
                        lat = float(row["latitude"])
                        lon = float(row["longitude"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if dataset.lon_convention == "0360":
                        lon = ((lon + 180.0) % 360.0) - 180.0
                    value = row.get("sst")
                    if value in (None, "", "NaN"):
                        continue
                    try:
                        out.append((lat, lon, float(value)))
                    except (TypeError, ValueError):
                        continue
                if out:
                    return out
        return []


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_time_actual_range(info_csv: str) -> tuple[dt.datetime, dt.datetime] | None:
    """Pull the time axis bounds out of an ERDDAP `info` CSV."""
    reader = csv.reader(io.StringIO(info_csv))
    for row in reader:
        if len(row) >= 5 and row[0] == "attribute" and row[1] == "time" and row[2] == "actual_range":
            try:
                low, high = (float(part) for part in row[4].split(","))
            except (TypeError, ValueError):
                return None
            return (
                dt.datetime.fromtimestamp(low, tz=dt.timezone.utc),
                dt.datetime.fromtimestamp(high, tz=dt.timezone.utc),
            )
    return None


def _parse_griddap_csv(text: str) -> list[dict[str, Any]]:
    """Parse griddap CSV: a header row, a units row, then data.

    Forgetting the units row shifts every value by one and produces a
    plausible-looking but entirely wrong first record.
    """
    if not text or text.lstrip().startswith("Error {"):
        return []
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    try:
        next(reader)  # units row
    except StopIteration:
        return []
    rows: list[dict[str, Any]] = []
    for record in reader:
        if len(record) != len(header):
            continue
        rows.append(dict(zip(header, record)))
    return rows


def _rows_to_observations(
    rows: Sequence[dict[str, Any]], *, port_id: str | None, source_kind: str
) -> list[MarineObservation]:
    observations: list[MarineObservation] = []
    for row in rows:
        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            timestamp = _erddap_time_to_ms(row["time"])
        except (KeyError, TypeError, ValueError):
            continue

        values: dict[str, Any] = {}
        for column, field_name in VARIABLE_MAP.items():
            if column in row:
                raw = row[column]
                if raw in (None, "", "NaN", "nan"):
                    continue
                try:
                    values[field_name] = float(raw)
                except (TypeError, ValueError):
                    continue
        if not values:
            continue
        try:
            observations.append(
                MarineObservation(
                    latitude=latitude,
                    longitude=longitude,
                    timestamp=timestamp,
                    port_id=port_id,
                    source=KIND_SOURCE.get(source_kind, "erddap_sst"),
                    is_forecast=False,
                    is_historical_cache=True,
                    **values,
                )
            )
        except ValueError:
            continue
    return observations


def _erddap_time_to_ms(value: str) -> int:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)
