"""Shared HTTP client: token bucket, backoff, and honest error classification.

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
import time
from typing import Any, Mapping

import httpx

from ..logging_setup import get_logger

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


class TokenBucket:
    """Async token bucket shared across every coroutine that talks outbound.

    Sharing one bucket is the point: parallel chunk fetches must not be able
    to collectively exceed the sustained rate just because each one
    individually respects it.
    """

    def __init__(self, rate_per_second: float = 2.0, burst: int = 4) -> None:
        self.rate = max(0.1, float(rate_per_second))
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)


class RateLimitedClient:
    """httpx wrapper with rate limiting, retries and conditional GET."""

    def __init__(
        self,
        rate_per_second: float = 2.0,
        burst: int = 4,
        timeout: float = 60.0,
        max_attempts: int = 8,
        backoff_cap: float = 900.0,
        user_agent: str = "OceanPulse/1.0",
    ) -> None:
        self.bucket = TokenBucket(rate_per_second, burst)
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_cap = float(backoff_cap)
        self.user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        # Last-Modified per URL, for conditional requests.
        self._last_modified: dict[str, str] = {}

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
        headers: dict[str, str] = {}
        if conditional and url in self._last_modified:
            headers["If-Modified-Since"] = self._last_modified[url]

        await self.bucket.acquire()
        try:
            response = await self._client.get(url, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise TransientError(f"network error for {url}: {exc}") from exc

        if response.status_code == 304:
            raise NotModified(url)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise TransientError(f"rate limited (Retry-After={retry_after})")

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
            raise PermanentError(f"HTTP {response.status_code} from {url}: {response.text[:200]}")

        if conditional:
            last_modified = response.headers.get("Last-Modified")
            if last_modified:
                self._last_modified[url] = last_modified

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
        """
        attempts = self.max_attempts if max_attempts is None else max(1, int(max_attempts))
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._request_once(url, params, conditional)
            except PermanentError:
                raise
            except TransientError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                ceiling = min(self.backoff_cap, 2.0**attempt)
                delay = random.uniform(0.0, ceiling)
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
