"""Per-provider rate limits, concurrency caps and a persistent request budget.

A single global token bucket - what this project started with - is not enough to
be a good citizen of someone else's free service. Three separate things can go
wrong, and they need three separate mechanisms:

**Instantaneous rate.** Too many requests per second looks like an attack and is
what gets an IP blocked. A token bucket fixes this, but the right rate differs
per provider: Open-Meteo is a fast CDN-fronted API, while ERDDAP is a single
research server that takes seconds to answer one query. Sharing one bucket
between them means either being needlessly slow with Open-Meteo or needlessly
harsh on ERDDAP.

**Concurrency.** A bucket limits the *rate* of requests, not how many are in
flight. Ten simultaneous slow ERDDAP queries pass a 2/s bucket happily and then
sit on ten of that server's worker threads at once. A semaphore is the only
thing that bounds that.

**Total volume.** Open-Meteo's free tier is a daily and hourly *quota*, not a
rate. A bucket cannot see it. Exceeding it is how a self-hosted app quietly
stops working after lunch, so the budget is counted locally, persisted across
restarts, and refuses requests before the provider has to.

Limits here are deliberately set well below each provider's published ceiling.
The application is a background collector on someone's laptop; there is no
reason for it to run anywhere near the edge.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from ..logging_setup import get_logger

log = get_logger(__name__)


class BudgetExceeded(RuntimeError):
    """The local quota for a provider is spent. Not the provider's error."""


@dataclass(frozen=True)
class HostLimits:
    """What we allow ourselves against one provider."""

    name: str
    rate_per_second: float
    burst: int
    max_concurrent: int
    daily_budget: int
    hourly_budget: int
    # What the provider actually publishes, for the README and the UI. Kept
    # next to our own numbers so the safety margin is auditable.
    published_note: str = ""


# Open-Meteo's free non-commercial tier publishes 10,000 requests/day,
# 5,000/hour and 600/minute. Batched at 100 coordinates per call, a 250-point
# grid plus tracked ports needs roughly 300-600 calls/day, so a 3,000/day
# self-imposed cap leaves an order of magnitude of headroom while still being a
# real backstop against a runaway loop.
OPEN_METEO = HostLimits(
    name="open-meteo",
    rate_per_second=2.0,
    burst=4,
    max_concurrent=2,
    daily_budget=3000,
    hourly_budget=600,
    published_note="free tier: 10,000/day, 5,000/hour, 600/minute",
)

# ERDDAP is a NOAA research server, not a CDN. It publishes no numeric rate
# limit; its documentation simply asks that clients be considerate and warns
# that abusive clients get blocked. One request at a time at 1/s is slow, and
# that is the point - historical backfill is a background task, and a single
# outstanding query is about as gentle as a client can be while still working.
ERDDAP = HostLimits(
    name="erddap",
    rate_per_second=1.0,
    burst=2,
    max_concurrent=1,
    daily_budget=1500,
    hourly_budget=200,
    published_note="no published numeric limit; asks clients to be considerate",
)

# One-off setup downloads: the World Port Index and GeoNames archives.
BULK = HostLimits(
    name="bulk-download",
    rate_per_second=1.0,
    burst=2,
    max_concurrent=1,
    daily_budget=50,
    hourly_budget=25,
    published_note="bulk files, fetched once at setup",
)

_HOST_MAP = {
    "marine-api.open-meteo.com": OPEN_METEO,
    "api.open-meteo.com": OPEN_METEO,
    "coastwatch.pfeg.noaa.gov": ERDDAP,
    "upwell.pfeg.noaa.gov": ERDDAP,
    "oceanwatch.pifsc.noaa.gov": ERDDAP,
    "msi.nga.mil": BULK,
    "download.geonames.org": BULK,
}

ALL_LIMITS = (OPEN_METEO, ERDDAP, BULK)


def limits_for(url: str) -> HostLimits:
    host = (urlsplit(url).hostname or "").lower()
    if host in _HOST_MAP:
        return _HOST_MAP[host]
    # Anything unrecognised gets the most cautious treatment rather than the
    # most permissive.
    return ERDDAP


class TokenBucket:
    """Async token bucket. One instance per provider."""

    def __init__(self, rate_per_second: float, burst: int) -> None:
        self.rate = max(0.05, float(rate_per_second))
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)


class RequestBudget:
    """Counts requests per provider per hour and per UTC day.

    Persisted through a caller-supplied store so a restart cannot be used - by
    accident or by a crash loop - to reset the quota and hammer a provider.
    """

    def __init__(
        self,
        load: Callable[[str], str | None] | None = None,
        save: Callable[[str, str], None] | None = None,
    ) -> None:
        self._load = load
        self._save = save
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, int | str]] = {}
        self._restore()

    # -- persistence ------------------------------------------------------

    _KEY = "request_budget"

    def _restore(self) -> None:
        if self._load is None:
            return
        raw = self._load(self._KEY)
        if not raw:
            return
        try:
            self._state = json.loads(raw)
        except (TypeError, ValueError):
            self._state = {}

    def _persist(self) -> None:
        if self._save is None:
            return
        try:
            self._save(self._KEY, json.dumps(self._state, sort_keys=True))
        except Exception as exc:  # noqa: BLE001 - accounting must not break ingest
            log.debug("could not persist request budget: %s", exc)

    # -- accounting -------------------------------------------------------

    @staticmethod
    def _stamps() -> tuple[str, str]:
        now = dt.datetime.now(dt.timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H")

    def _slot(self, name: str) -> dict[str, int | str]:
        day, hour = self._stamps()
        slot = self._state.setdefault(name, {})
        if slot.get("day") != day:
            slot["day"] = day
            slot["day_count"] = 0
        if slot.get("hour") != hour:
            slot["hour"] = hour
            slot["hour_count"] = 0
        return slot

    def check(self, limits: HostLimits) -> None:
        """Raise BudgetExceeded if this request would break the local quota."""
        with self._lock:
            slot = self._slot(limits.name)
            if int(slot.get("day_count", 0)) >= limits.daily_budget:
                raise BudgetExceeded(
                    f"{limits.name}: local daily budget of {limits.daily_budget} "
                    f"requests is spent; resets at 00:00 UTC"
                )
            if int(slot.get("hour_count", 0)) >= limits.hourly_budget:
                raise BudgetExceeded(
                    f"{limits.name}: local hourly budget of {limits.hourly_budget} "
                    f"requests is spent; resets on the hour"
                )

    def record(self, limits: HostLimits, count: int = 1) -> None:
        with self._lock:
            slot = self._slot(limits.name)
            slot["day_count"] = int(slot.get("day_count", 0)) + count
            slot["hour_count"] = int(slot.get("hour_count", 0)) + count
            self._persist()

    def snapshot(self) -> list[dict[str, object]]:
        """Current usage per provider, for the status bar and the README."""
        with self._lock:
            out = []
            for limits in ALL_LIMITS:
                slot = self._slot(limits.name)
                out.append(
                    {
                        "provider": limits.name,
                        "day_used": int(slot.get("day_count", 0)),
                        "day_budget": limits.daily_budget,
                        "hour_used": int(slot.get("hour_count", 0)),
                        "hour_budget": limits.hourly_budget,
                        "rate_per_second": limits.rate_per_second,
                        "max_concurrent": limits.max_concurrent,
                        "published_note": limits.published_note,
                    }
                )
            return out


class HostGate:
    """Bucket plus semaphore for one provider."""

    def __init__(self, limits: HostLimits) -> None:
        self.limits = limits
        self.bucket = TokenBucket(limits.rate_per_second, limits.burst)
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def semaphore(self) -> asyncio.Semaphore:
        # Created lazily so the object can be built outside a running loop.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.limits.max_concurrent)
        return self._semaphore


class GateRegistry:
    """One HostGate per provider, created on demand."""

    def __init__(self) -> None:
        self._gates: dict[str, HostGate] = {}
        self._lock = threading.Lock()

    def gate_for(self, url: str) -> HostGate:
        limits = limits_for(url)
        with self._lock:
            gate = self._gates.get(limits.name)
            if gate is None:
                gate = HostGate(limits)
                self._gates[limits.name] = gate
            return gate
