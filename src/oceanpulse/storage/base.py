"""Storage interface and query filter types.

The storage API is deliberately *synchronous*. Dash callbacks are WSGI, i.e.
plain blocking functions; reaching an async pool from one means either
spinning an event loop per request or bouncing through
`run_coroutine_threadsafe`, and both are deadlock factories. The async
ingestion side reaches this layer through `asyncio.to_thread`, which keeps the
part that genuinely benefits from async - network I/O - fully non-blocking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ..models import MarineObservation


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def normalised(self) -> "BoundingBox":
        return BoundingBox(
            min_lat=min(self.min_lat, self.max_lat),
            max_lat=max(self.min_lat, self.max_lat),
            min_lon=min(self.min_lon, self.max_lon),
            max_lon=max(self.min_lon, self.max_lon),
        )


@dataclass
class ObservationFilter:
    """Query parameters for reading observations back out."""

    start_ms: int | None = None
    end_ms: int | None = None
    bbox: BoundingBox | None = None
    port_id: str | None = None
    centre: tuple[float, float] | None = None
    radius_km: float | None = None
    include_forecast: bool = False
    require_columns: Sequence[str] = ()
    limit: int | None = None
    order: str = "asc"


class Storage(Protocol):
    """What every backend must provide."""

    def initialise(self) -> None: ...

    def upsert_observations(
        self, observations: Sequence[MarineObservation], source: str
    ) -> int: ...

    def query_observations(self, filters: ObservationFilter) -> list[dict[str, Any]]: ...

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def set_setting(self, key: str, value: str) -> None: ...

    def stats(self) -> dict[str, Any]: ...

    def close(self) -> None: ...
