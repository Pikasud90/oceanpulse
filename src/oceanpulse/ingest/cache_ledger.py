"""Deciding what still needs fetching.

Pure range arithmetic, kept separate from the database so the containment
rules can be tested exhaustively without one.

The important rule is that a cache hit requires **containment, not overlap**.
The intuitive test - "have we fetched something near here, over roughly this
period" - reports a hit when a one-week cached window sits inside a five-year
request, and the caller is then handed a week of data believing it has five
years. That failure is silent and produces confidently wrong analytics.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Sequence

from ..storage.base import BoundingBox

MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class CachedRange:
    """One completed fetch, as recorded in the ledger."""

    dataset: str
    bbox: BoundingBox
    start_ms: int
    end_ms: int


def contains(cached: CachedRange, dataset: str, bbox: BoundingBox, start_ms: int, end_ms: int) -> bool:
    """True when `cached` fully covers the request in space *and* time."""
    if cached.dataset != dataset:
        return False
    box = bbox.normalised()
    cached_box = cached.bbox.normalised()
    return (
        cached_box.min_lat <= box.min_lat
        and cached_box.max_lat >= box.max_lat
        and cached_box.min_lon <= box.min_lon
        and cached_box.max_lon >= box.max_lon
        and cached.start_ms <= start_ms
        and cached.end_ms >= end_ms
    )


def is_covered(
    cached_ranges: Sequence[CachedRange],
    dataset: str,
    bbox: BoundingBox,
    start_ms: int,
    end_ms: int,
) -> bool:
    """True when any single earlier fetch contains this request.

    Deliberately does not stitch several partial ranges together. Two adjacent
    fetches only cover a request jointly if their spatial extents also match,
    and getting that wrong reintroduces the silent-truncation bug this module
    exists to prevent.
    """
    return any(contains(c, dataset, bbox, start_ms, end_ms) for c in cached_ranges)


def point_bbox(latitude: float, longitude: float, pad_deg: float = 0.25) -> BoundingBox:
    """A small box around a point, so a re-request of the same port hits cache."""
    return BoundingBox(
        min_lat=max(-90.0, latitude - pad_deg),
        max_lat=min(90.0, latitude + pad_deg),
        min_lon=longitude - pad_deg,
        max_lon=longitude + pad_deg,
    )


def chunk_date_range(
    start: dt.date, end: dt.date, chunk_days: int = 365
) -> list[tuple[dt.date, dt.date]]:
    """Split a date range into windows that upstream servers can serve.

    Windows are inclusive at both ends and never overlap: the next one starts
    the day after the previous ends. An off-by-one here would re-fetch a day
    at every boundary, which is invisible in the data because the upsert
    deduplicates it, and doubles the request count for long ranges.
    """
    if start > end:
        return []
    chunk_days = max(1, int(chunk_days))
    windows: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + dt.timedelta(days=chunk_days - 1))
        windows.append((cursor, window_end))
        cursor = window_end + dt.timedelta(days=1)
    return windows


def ms_to_date(timestamp_ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(timestamp_ms / 1000.0, tz=dt.timezone.utc).date()


def date_to_ms(value: dt.date, end_of_day: bool = False) -> int:
    moment = dt.datetime.combine(
        value, dt.time(23, 59, 59) if end_of_day else dt.time(0, 0, 0), tzinfo=dt.timezone.utc
    )
    return int(moment.timestamp() * 1000)
