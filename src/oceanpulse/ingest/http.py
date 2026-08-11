"""Shared HTTP client: per-provider gating, backoff, and honest error classification.

Politeness is enforced in four independent layers, because each catches
something the others cannot. See `limits.py` for why one global token bucket is
not sufficient.

    budget      refuses a request before the provider has to (daily/hourly)
    semaphore   bounds requests in flight, which a rate limit does not
    bucket      bounds requests per second
    backoff     spaces out retries after a failure, with full jitter

The retry policy is where most of the thinking is. The usual rule - 4xx is
permanent, 5xx is transient - is *wrong* for ERDDAP, which answers

    404 Not Found: Currently unknown datasetID=ncdcOisst21Agg_LonPM180

while a dataset reloads. That was observed live: the identical request
succeeded minutes earlier and minutes later. Treating it as permanent would
disable historical fetching until the process restarted.
"""

from __future__ import annotations

import asyncio
import random
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

import httpx

from ..logging_setup import get_logger
from .limits import BudgetExceeded, GateRegistry, RequestBudget, limits_for

log = get_logger(__name__)


class TransientError(RuntimeError):
    """Worth retrying: network blip, throttling, upstream hiccup."""


class PermanentError(RuntimeError):
    """Not worth retrying: a malformed request that will fail identically."""


class NotModified(Exception):
    """HTTP 304. The caller already holds this payload."""


# ERDDAP says this with a 404 status while a dataset is being reloaded.
_ERDDAP_TRANSIENT_MARKERS = (
    "currently unknown datasetid",
    "there was a (temporary?) problem",
    "please try again",
)

# Never sleep longer than this on a server-supplied Retry-After. A provider
# asking for an hour is telling us to stop for now, not to hold a coroutine and
# a connection open for an hour.
MAX_HONOURED_RETRY_AFTER = 120.0


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP date. Support both."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as dt

    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())


class RateLimitedClient:
    """httpx wrapper with per-provider gating, retries and conditional GET."""

    def __init__(
        self,
        timeout: float = 60.0,
        max_attempts: int = 8,
        backoff_cap: float = 900.0,
        user_agent: str = "OceanPulse/1.0",
        budget: RequestBudget | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_cap = float(backoff_cap)
        self.user_agent = user_agent
        self.gates = GateRegistry()
        self.budget = budget if budget is not None else RequestBudget()
        self._client: httpx.AsyncClient | None = None
        # Last-Modified / ETag per URL, for conditional requests.
        self._validators: dict[str, dict[str, str]] = {}

    async def __aenter__(self) -> "RateLimitedClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
                follow_redirects=True,
                # Bounded pool: a self-hosted collector has no business holding
                # dozens of sockets open against a research server.
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- core request -----------------------------------------------------

    async def _request_once(
        self,
        url: str,
        params: Mapping[str, Any] | None,
        conditional: bool,
    ) -> httpx.Response:
        await self.start()
        assert self._client is not None

        gate = self.gates.gate_for(url)
        # Budget first: if the quota is spent there is no point queueing.
        self.budget.check(gate.limits)

        headers: dict[str, str] = {}
        if conditional:
            for header, value in self._validators.get(url, {}).items():
                headers[header] = value

        async with gate.semaphore:
            await gate.bucket.acquire()
            self.budget.record(gate.limits)
            try:
                response = await self._client.get(url, params=params, headers=headers)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                raise TransientError(
                    f"network error for {url}: {type(exc).__name__}: {exc}"
                ) from exc

        if response.status_code == 304:
            raise NotModified(url)

        if response.status_code == 429:
            delay = _parse_retry_after(response.headers.get("Retry-After"))
            raise TransientError(
                f"HTTP 429 rate limited by {gate.limits.name}"
                + (f", Retry-After={delay:.0f}s" if delay else ""),
            )

        if response.status_code >= 500:
            raise TransientError(f"HTTP {response.status_code} from {url}")

        if response.status_code >= 400:
            body = response.text[:400].lower()
            if any(marker in body for marker in _ERDDAP_TRANSIENT_MARKERS):
                # A reloading ERDDAP dataset. Permanent by status code,
                # transient in fact.
                raise TransientError(
                    f"HTTP {response.status_code} but transient upstream state: "
                    f"{response.text[:160].strip()}"
                )
            raise PermanentError(
                f"HTTP {response.status_code} from {url}: {response.text[:200]}"
            )

        if conditional:
            validators: dict[str, str] = {}
            if response.headers.get("Last-Modified"):
                validators["If-Modified-Since"] = response.headers["Last-Modified"]
            if response.headers.get("ETag"):
                validators["If-None-Match"] = response.headers["ETag"]
            if validators:
                self._validators[url] = validators

        # ERDDAP also returns error documents with a 200 status.
        if response.text[:64].lstrip().startswith("Error {"):
            body = response.text[:400].lower()
            if any(marker in body for marker in _ERDDAP_TRANSIENT_MARKERS):
                raise TransientError(f"ERDDAP transient error: {response.text[:160].strip()}")
            raise PermanentError(f"ERDDAP error: {response.text[:200].strip()}")

        return response

    async def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        conditional: bool = False,
        max_attempts: int | None = None,
    ) -> httpx.Response:
        """GET with exponential backoff and full jitter.

        Full jitter, not fixed backoff: without it every client that lost the
        same outage retries in lockstep and re-creates the outage.

        `max_attempts` overrides the client default, and callers with a
        fallback should lower it. Patient retrying only makes sense when there
        is nothing else to try: an ERDDAP dataset that answers 404 "currently
        unknown datasetID" is classified transient, so a caller that has an
        equivalent variant available would otherwise spend minutes backing off
        against the broken one before ever reaching the one that works.

        A spent local budget is never retried - waiting would not help, and the
        whole point is to stop.
        """
        attempts = self.max_attempts if max_attempts is None else max(1, int(max_attempts))
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._request_once(url, params, conditional)
            except (PermanentError, BudgetExceeded):
                raise
            except TransientError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                ceiling = min(self.backoff_cap, 2.0**attempt)
                delay = random.uniform(0.0, ceiling)
                # Honour an explicit Retry-After over our own guess.
                if "Retry-After=" in str(exc):
                    try:
                        asked = float(str(exc).split("Retry-After=")[1].rstrip("s ,"))
                        delay = min(MAX_HONOURED_RETRY_AFTER, max(delay, asked))
                    except (IndexError, ValueError):
                        pass
                log.warning(
                    "%s (attempt %d/%d), retrying in %.1fs",
                    exc,
                    attempt + 1,
                    attempts,
                    delay,
                )
                await asyncio.sleep(delay)
        raise TransientError(f"gave up after {attempts} attempts: {last_error}")

    async def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        conditional: bool = False,
        max_attempts: int | None = None,
    ) -> Any:
        response = await self.get(
            url, params=params, conditional=conditional, max_attempts=max_attempts
        )
        try:
            return response.json()
        except ValueError as exc:
            # A truncated body or an HTML error page reaching the JSON parser
            # is an upstream condition, not a permanent one.
            raise TransientError(f"invalid JSON from {url}: {exc}") from exc

    async def get_text(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        conditional: bool = False,
        max_attempts: int | None = None,
    ) -> str:
        response = await self.get(
            url, params=params, conditional=conditional, max_attempts=max_attempts
        )
        return response.text

    async def get_bytes(
        self, url: str, params: Mapping[str, Any] | None = None
    ) -> bytes:
        response = await self.get(url, params=params)
        return response.content

    # -- introspection ----------------------------------------------------

    def budget_snapshot(self) -> list[dict[str, object]]:
        return self.budget.snapshot()

    def describe_limits(self, url: str) -> str:
        limits = limits_for(url)
        return (
            f"{limits.name}: {limits.rate_per_second}/s, "
            f"{limits.max_concurrent} concurrent, "
            f"{limits.daily_budget}/day ({limits.published_note})"
        )
