"""Open-Meteo Marine client.

Three behaviours of this API drive the design, all of them observed rather
than documented:

1. **Many coordinates fit in one request.** Comma-separated `latitude` and
   `longitude` lists return a JSON *array* of per-location objects. This is
   not an optimisation - polling 250 points individually every 15 minutes
   would be 24,000 calls a day, over the free allowance. Batched, it is a few
   hundred.

2. **A single coordinate returns an object, not an array.** Anything that
   iterates the response has to normalise first.

3. **Failure is silent.** Land coordinates and dates before a variable's
   archive floor both return HTTP 200 with every value `null`. There is no
   error to catch, so "did this actually contain data" is a decision the
   client has to make explicitly.

Measured archive floors differ per variable: waves from 2021-12, currents from
2022-01, sea-surface temperature only from 2022-12. They live in `config`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Sequence

from ..config import (
    MARINE_ARCHIVE_FLOORS,
    MARINE_HOURLY_VARIABLES,
    MAX_COORDS_PER_REQUEST,
    OPEN_METEO_ELEVATION_URL,
    OPEN_METEO_MARINE_URL,
)
from ..logging_setup import get_logger
from ..models import MarineObservation
from .http import PermanentError, RateLimitedClient, TransientError

log = get_logger(__name__)

# Response field -> model field.
FIELD_MAP = {
    "wave_height": "wave_height_m",
    "wave_period": "wave_period_s",
    "wave_direction": "wave_direction_deg",
    "ocean_current_velocity": "current_velocity_kmh",
    "ocean_current_direction": "current_direction_deg",
    "sea_surface_temperature": "sst_celsius",
}


def archive_floor(variables: Sequence[str] = MARINE_HOURLY_VARIABLES) -> dt.date:
    """Earliest date for which *any* requested variable has data."""
    floors = [MARINE_ARCHIVE_FLOORS[v] for v in variables if v in MARINE_ARCHIVE_FLOORS]
    if not floors:
        return dt.date(2022, 1, 1)
    return dt.date.fromisoformat(min(floors))


def clamp_start_date(
    start: dt.date, variables: Sequence[str] = MARINE_HOURLY_VARIABLES
) -> dt.date:
    """Never ask for dates the archive cannot serve.

    Without this the API returns a wall of nulls with a 200 status and the
    ingester reports a successful fetch of nothing.
    """
    floor = archive_floor(variables)
    return max(start, floor)


def variables_available_from(start: dt.date) -> dict[str, bool]:
    """Which variables actually have data from `start` onwards."""
    return {
        variable: start >= dt.date.fromisoformat(floor)
        for variable, floor in MARINE_ARCHIVE_FLOORS.items()
    }


def _chunk(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _as_locations(payload: Any) -> list[dict[str, Any]]:
    """Normalise the single-location object into a one-element list."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _parse_location(
    location: dict[str, Any],
    *,
    requested_lat: float,
    requested_lon: float,
    port_id: str | None,
    now_ms: int,
    is_historical: bool,
) -> tuple[list[MarineObservation], bool]:
    """Turn one location object into observations.

    Returns the observations and whether the location produced any data at
    all. The second value is what distinguishes an ocean cell from a land cell
    - nothing in the HTTP layer will tell you.
    """
    hourly = location.get("hourly")
    if not isinstance(hourly, dict):
        return [], False

    times = hourly.get("time") or []
    if not times:
        return [], False

    # Snapped grid-cell centre, which is what the values actually describe.
    latitude = float(location.get("latitude", requested_lat))
    longitude = float(location.get("longitude", requested_lon))

    series = {
        model_field: hourly.get(api_field) or []
        for api_field, model_field in FIELD_MAP.items()
    }

    observations: list[MarineObservation] = []
    for index, stamp in enumerate(times):
        values: dict[str, Any] = {}
        for model_field, column in series.items():
            if index < len(column):
                values[model_field] = column[index]
        if all(value is None for value in values.values()):
            continue
        try:
            timestamp_ms = _iso_to_ms(stamp)
        except ValueError:
            log.warning("unparseable timestamp %r from Open-Meteo", stamp)
            continue
        try:
            observations.append(
                MarineObservation(
                    latitude=latitude,
                    longitude=longitude,
                    timestamp=timestamp_ms,
                    port_id=port_id,
                    source="open_meteo",
                    is_forecast=timestamp_ms > now_ms,
                    is_historical_cache=is_historical,
                    **values,
                )
            )
        except ValueError:
            # Dropped at the validation boundary; one bad hour never blocks
            # the rest of the series.
            continue

    return observations, bool(observations)


def _iso_to_ms(value: str) -> int:
    """`2026-08-09T13:00` (UTC, per timezone=GMT) to epoch milliseconds."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


class OpenMeteoMarine:
    """Fetches marine model output for points and for whole grids."""

    def __init__(self, client: RateLimitedClient) -> None:
        self.client = client

    async def fetch_points(
        self,
        coordinates: Sequence[tuple[float, float]],
        *,
        past_days: int = 2,
        forecast_days: int = 2,
        port_ids: Sequence[str | None] | None = None,
        variables: Sequence[str] = MARINE_HOURLY_VARIABLES,
    ) -> tuple[list[MarineObservation], list[int], list[int]]:
        """Fetch many points in as few requests as possible.

        Returns `(observations, indices_with_data, indices_without_data)`.
        The index lists let the caller mark grid cells valid or invalid
        without a second round of requests.
        """
        if not coordinates:
            return [], [], []

        now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
        all_observations: list[MarineObservation] = []
        with_data: list[int] = []
        without_data: list[int] = []

        indices = list(range(len(coordinates)))
        for chunk in _chunk(indices, MAX_COORDS_PER_REQUEST):
            params = {
                "latitude": ",".join(f"{coordinates[i][0]:.4f}" for i in chunk),
                "longitude": ",".join(f"{coordinates[i][1]:.4f}" for i in chunk),
                "hourly": ",".join(variables),
                "past_days": int(past_days),
                "forecast_days": int(forecast_days),
                "timezone": "GMT",
                "cell_selection": "sea",
            }
            try:
                payload = await self.client.get_json(OPEN_METEO_MARINE_URL, params=params)
            except (TransientError, PermanentError) as exc:
                log.warning("marine batch of %d points failed: %s", len(chunk), exc)
                continue

            locations = _as_locations(payload)
            if len(locations) != len(chunk):
                log.warning(
                    "asked for %d locations, received %d - marking batch unverified",
                    len(chunk),
                    len(locations),
                )
            for offset, location in enumerate(locations):
                if offset >= len(chunk):
                    break
                index = chunk[offset]
                lat, lon = coordinates[index]
                port_id = port_ids[index] if port_ids else None
                observations, had_data = _parse_location(
                    location,
                    requested_lat=lat,
                    requested_lon=lon,
                    port_id=port_id,
                    now_ms=now_ms,
                    is_historical=False,
                )
                all_observations.extend(observations)
                (with_data if had_data else without_data).append(index)

        return all_observations, with_data, without_data

    async def fetch_range(
        self,
        latitude: float,
        longitude: float,
        start: dt.date,
        end: dt.date,
        *,
        port_id: str | None = None,
        variables: Sequence[str] = MARINE_HOURLY_VARIABLES,
    ) -> list[MarineObservation]:
        """Historical series for one point, clamped to the real archive floor."""
        effective_start = clamp_start_date(start, variables)
        if effective_start > end:
            log.info(
                "requested range %s..%s is entirely before the marine archive floor %s",
                start,
                end,
                archive_floor(variables),
            )
            return []
        if effective_start != start:
            log.info(
                "clamped marine start date %s -> %s (archive floor)", start, effective_start
            )

        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "hourly": ",".join(variables),
            "start_date": effective_start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "GMT",
            "cell_selection": "sea",
        }
        payload = await self.client.get_json(OPEN_METEO_MARINE_URL, params=params)
        now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
        observations: list[MarineObservation] = []
        for location in _as_locations(payload):
            parsed, _ = _parse_location(
                location,
                requested_lat=latitude,
                requested_lon=longitude,
                port_id=port_id,
                now_ms=now_ms,
                is_historical=True,
            )
            observations.extend(parsed)
        return observations

    async def fetch_elevation(self, latitude: float, longitude: float) -> float | None:
        """Ground elevation in metres, used by the inundation model."""
        try:
            payload = await self.client.get_json(
                OPEN_METEO_ELEVATION_URL,
                params={"latitude": f"{latitude:.4f}", "longitude": f"{longitude:.4f}"},
            )
        except (TransientError, PermanentError) as exc:
            log.warning("elevation lookup failed for %.3f,%.3f: %s", latitude, longitude, exc)
            return None
        elevations = payload.get("elevation") if isinstance(payload, dict) else None
        if isinstance(elevations, list) and elevations:
            try:
                return float(elevations[0])
            except (TypeError, ValueError):
                return None
        return None

    async def find_marine_cell(
        self, latitude: float, longitude: float, search_radius_deg: float = 1.0
    ) -> tuple[float, float] | None:
        """Nearest nearby point where the marine model actually has data.

        Harbour coordinates routinely sit in a land cell. Without this a
        tracked port silently produces no time series at all, which looks
        exactly like a broken ingester.
        """
        candidates: list[tuple[float, float]] = [(latitude, longitude)]
        steps = (0.25, 0.5, 1.0)
        for step in steps:
            if step > search_radius_deg:
                break
            for dlat, dlon in (
                (step, 0.0),
                (-step, 0.0),
                (0.0, step),
                (0.0, -step),
                (step, step),
                (step, -step),
                (-step, step),
                (-step, -step),
            ):
                lat = max(-90.0, min(90.0, latitude + dlat))
                lon = ((longitude + dlon + 180.0) % 360.0) - 180.0
                candidates.append((lat, lon))

        _, with_data, _ = await self.fetch_points(
            candidates, past_days=0, forecast_days=1, variables=("wave_height",)
        )
        if not with_data:
            return None
        # Candidates are generated nearest-first, so the lowest index wins.
        return candidates[min(with_data)]
