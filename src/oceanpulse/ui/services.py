"""Shared application state for the interface.

One place holds the configuration, the storage handle and the gazetteer, so
callback modules can reach them without importing each other and without
rebuilding a connection per request.
"""

from __future__ import annotations

import threading
from typing import Any, Awaitable, TypeVar

from ..config import Config, load_config
from ..gazetteer.store import GazetteerStore
from ..ingest import runner
from ..logging_setup import get_logger
from ..storage.sqlite_backend import SQLiteStorage

log = get_logger(__name__)

T = TypeVar("T")


class Services:
    def __init__(self, config: Config) -> None:
        self.config = config
        config.ensure_dirs()
        self.storage = SQLiteStorage(config.db_path)
        self.storage.initialise()
        self.gazetteer = GazetteerStore(config.ports_db_path)
        self._lock = threading.Lock()
        self._erddap = None

    # -- async bridge -----------------------------------------------------

    def run_async(self, coro: Awaitable[T], timeout: float = 240.0) -> T:
        """Run a coroutine on the shared background loop and wait for it."""
        return runner.get_loop().run(coro, timeout=timeout)

    @property
    def client(self):  # noqa: ANN201 - RateLimitedClient, avoids a cycle
        return runner.get_client(self.config, self.storage)

    @property
    def erddap(self):  # noqa: ANN201 - ErddapClient, avoids a cycle
        """One ERDDAP client for the whole process.

        Its caches - resolved cells and dataset coverage - are what keep a
        timeline load from re-probing a masked coastline every single time, so
        it has to outlive the request that created it.
        """
        with self._lock:
            if self._erddap is None:
                from ..ingest.noaa_erddap import ErddapClient

                self._erddap = ErddapClient(self.client, store=self.storage)
            return self._erddap

    def request_budget(self) -> list[dict[str, object]]:
        """Per-provider request usage, for the status bar."""
        return self.client.budget_snapshot()

    # -- settings ---------------------------------------------------------

    def poll_interval(self) -> int:
        raw = self.storage.get_setting("poll_interval_minutes")
        try:
            return int(raw) if raw is not None else self.config.poll_interval_minutes
        except (TypeError, ValueError):
            return self.config.poll_interval_minutes

    def set_poll_interval(self, minutes: int) -> None:
        self.storage.set_setting("poll_interval_minutes", str(int(minutes)))

    def status_summary(self) -> dict[str, Any]:
        stats = self.storage.stats()
        health = self.storage.daemon_health()
        return {**stats, **{f"daemon_{k}": v for k, v in health.items()}}


_services: Services | None = None
_services_lock = threading.Lock()


def init_services(config: Config | None = None) -> Services:
    global _services
    with _services_lock:
        if _services is None:
            _services = Services(config or load_config())
        return _services


def get_services() -> Services:
    if _services is None:
        return init_services()
    return _services
