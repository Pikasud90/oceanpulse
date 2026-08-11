"""One background event loop, shared by the daemon and the web interface.

Dash callbacks are synchronous WSGI functions, but every network client here
is async. The naive fix - `asyncio.run(...)` inside each callback - would give
each call its own event loop, its own HTTP connection pool and, critically,
its own token bucket, so N concurrent user actions would collectively blow
through the rate limit that each one individually respects.

Instead there is exactly one loop on one daemon thread. The daemon schedules
its polling cycle on it, and UI callbacks submit coroutines and block on the
result. One loop, one client, one bucket.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, TypeVar

from ..config import Config
from ..logging_setup import get_logger
from .http import RateLimitedClient
from .limits import RequestBudget

log = get_logger(__name__)

T = TypeVar("T")


class BackgroundLoop:
    """An event loop running on its own thread."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, name="oceanpulse-loop", daemon=True
            )
            self._thread.start()
            self._ready.wait(timeout=10.0)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            finally:
                loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self.start()
        assert self._loop is not None
        return self._loop

    def submit(self, coro: Awaitable[T]) -> "Future[T]":
        return asyncio.run_coroutine_threadsafe(coro, self.loop)  # type: ignore[arg-type]

    def run(self, coro: Awaitable[T], timeout: float | None = 180.0) -> T:
        """Submit a coroutine and block until it finishes."""
        return self.submit(coro).result(timeout=timeout)

    def call_soon(self, callback: Callable[..., Any], *args: Any) -> None:
        self.loop.call_soon_threadsafe(callback, *args)

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None


_loop = BackgroundLoop()
_client: RateLimitedClient | None = None
_client_lock = threading.Lock()


def get_loop() -> BackgroundLoop:
    _loop.start()
    return _loop


def get_client(config: Config, storage: Any | None = None) -> RateLimitedClient:
    """The single shared, rate-limited HTTP client.

    The request budget is persisted through storage when available, so a
    restart - or a crash loop - cannot reset a provider's daily quota and let
    the app hammer a free service it has already used up.
    """
    global _client
    with _client_lock:
        if _client is None:
            budget = RequestBudget(
                load=(lambda key: storage.get_setting(key)) if storage else None,
                save=(lambda key, value: storage.set_setting(key, value)) if storage else None,
            )
            _client = RateLimitedClient(
                timeout=config.http_timeout,
                max_attempts=config.backoff_max_attempts,
                backoff_cap=config.backoff_cap_seconds,
                user_agent=config.user_agent,
                budget=budget,
            )
        return _client


def shutdown() -> None:
    global _client
    client = _client
    if client is not None:
        try:
            _loop.run(client.close(), timeout=10.0)
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass
        _client = None
    _loop.stop()
