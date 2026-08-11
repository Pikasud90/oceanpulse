"""The background ingestion loop.

One cycle:

1. Refresh the sparse global grid (one batched request per 100 points).
2. Refresh every tracked port, also batched.
3. Write a heartbeat.

The heartbeat is written *during* the sleep as well as at the top of the
cycle. Writing it once per cycle would leave the timestamp stale for 29 of
every 30 minutes, and the interface would report a perfectly healthy daemon as
STOPPED.

A cycle that fails is logged, marks the daemon DEGRADED, and the loop
continues. A daemon that exits on a transient network error is strictly worse
than one that says so and retries.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

from ..config import ALLOWED_POLL_INTERVALS, Config
from ..logging_setup import get_logger
from ..storage.sqlite_backend import SQLiteStorage
from .grid import OceanMask, build_macro_grid, default_mask_date
from .http import PermanentError, RateLimitedClient, TransientError
from .noaa_erddap import ErddapClient
from .open_meteo import OpenMeteoMarine

log = get_logger(__name__)

HEARTBEAT_SECONDS = 30


class IngestionDaemon:
    def __init__(
        self, config: Config, storage: SQLiteStorage, client: RateLimitedClient
    ) -> None:
        self.config = config
        self.storage = storage
        self.client = client
        self.marine = OpenMeteoMarine(client)
        self.erddap = ErddapClient(client, store=storage)
        self._stop = asyncio.Event()
        self._cycle = 0

    # -- lifecycle --------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def poll_interval_minutes(self) -> int:
        """Re-read every cycle so a UI change takes effect without a restart."""
        raw = self.storage.get_setting("poll_interval_minutes")
        try:
            value = int(raw) if raw is not None else self.config.poll_interval_minutes
        except (TypeError, ValueError):
            value = self.config.poll_interval_minutes
        return value if value in ALLOWED_POLL_INTERVALS else 30

    # -- one-time setup ---------------------------------------------------

    async def ensure_ocean_mask(self) -> OceanMask:
        """Load the land/sea mask, deriving it once if absent.

        Failure here is not fatal. Without a mask the grid is simply probed
        against Open-Meteo instead, which costs a handful of extra requests
        and reaches the same answer.
        """
        mask = OceanMask.load(self.config.ocean_mask_path)
        if mask is not None and not mask.is_empty:
            return mask

        if not self.config.erddap_enabled:
            return OceanMask()

        log.info("Deriving global ocean mask from OISST (one request, ~3 MB)")
        try:
            points = await self.erddap.fetch_global_sst_grid(default_mask_date(), stride=4)
        except (TransientError, PermanentError) as exc:
            log.warning("ocean mask unavailable (%s); falling back to probing", exc)
            return OceanMask()

        if not points:
            log.warning("ocean mask fetch returned nothing; falling back to probing")
            return OceanMask()

        mask = OceanMask.from_points(points)
        mask.save(self.config.ocean_mask_path)
        return mask

    async def ensure_grid(self, mask: OceanMask) -> None:
        existing = self.storage.get_grid_points()
        if existing:
            return
        points = build_macro_grid(mask, target_points=self.config.grid_target_points)
        self.storage.upsert_grid_points(points)
        log.info("Built macro grid: %d candidate ocean points", len(points))

    # -- cycle work -------------------------------------------------------

    async def poll_grid(self) -> dict[str, Any]:
        points = self.storage.get_grid_points(only_valid=True)
        if not points:
            return {"points": 0, "observations": 0}

        coordinates = [(p["latitude"], p["longitude"]) for p in points]
        observations, with_data, without_data = await self.marine.fetch_points(
            coordinates,
            past_days=self.config.grid_past_days,
            forecast_days=self.config.grid_forecast_days,
        )

        stored = 0
        if observations:
            stored = await asyncio.to_thread(
                self.storage.upsert_observations, observations, "open_meteo"
            )

        # Record which cells actually produced data. A cell that the mask
        # called ocean but the wave model has no domain for is disabled here,
        # permanently, after the first probe.
        if with_data or without_data:
            await asyncio.to_thread(
                self.storage.mark_grid_probe,
                [points[i]["grid_id"] for i in with_data],
                [points[i]["grid_id"] for i in without_data],
            )
        if without_data:
            log.info(
                "disabled %d grid cells with no marine model data", len(without_data)
            )
        return {"points": len(with_data), "observations": stored}

    async def poll_ports(self) -> dict[str, Any]:
        ports = self.storage.get_tracked_ports()
        if not ports:
            return {"ports": 0, "observations": 0}

        coordinates = [
            (
                float(p["marine_latitude"] or p["latitude"]),
                float(p["marine_longitude"] or p["longitude"]),
            )
            for p in ports
        ]
        port_ids = [str(p["port_id"]) for p in ports]

        observations, with_data, _ = await self.marine.fetch_points(
            coordinates,
            past_days=self.config.grid_past_days,
            forecast_days=self.config.port_forecast_days,
            port_ids=port_ids,
        )
        stored = 0
        if observations:
            stored = await asyncio.to_thread(
                self.storage.upsert_observations, observations, "open_meteo"
            )
        for index in with_data:
            await asyncio.to_thread(self.storage.mark_port_polled, port_ids[index])
        return {"ports": len(with_data), "observations": stored}

    async def run_cycle(self) -> dict[str, Any]:
        started = time.monotonic()
        grid = await self.poll_grid()
        ports = await self.poll_ports()
        elapsed = time.monotonic() - started
        self._cycle += 1
        summary = {
            "cycle": self._cycle,
            "grid_points": grid["points"],
            "grid_observations": grid["observations"],
            "ports": ports["ports"],
            "port_observations": ports["observations"],
            "seconds": round(elapsed, 1),
        }
        log.info(
            "cycle %d: %d grid points -> %d rows, %d ports -> %d rows, %.1fs",
            summary["cycle"],
            summary["grid_points"],
            summary["grid_observations"],
            summary["ports"],
            summary["port_observations"],
            elapsed,
        )
        return summary

    # -- main loop --------------------------------------------------------

    async def run(self) -> None:
        await asyncio.to_thread(self.storage.write_heartbeat, "active", "starting up")
        try:
            mask = await self.ensure_ocean_mask()
            await self.ensure_grid(mask)
        except Exception as exc:  # noqa: BLE001 - setup must not kill the daemon
            log.exception("daemon setup failed: %s", exc)
            await asyncio.to_thread(
                self.storage.write_heartbeat, "degraded", f"setup failed: {exc}"
            )

        while not self.stopping:
            try:
                summary = await self.run_cycle()
                await asyncio.to_thread(
                    self.storage.write_heartbeat,
                    "active",
                    f"cycle {summary['cycle']}: {summary['grid_observations']} grid rows, "
                    f"{summary['port_observations']} port rows",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep polling
                log.exception("ingestion cycle failed: %s", exc)
                await asyncio.to_thread(
                    self.storage.write_heartbeat, "degraded", str(exc)[:400]
                )

            await self._sleep_with_heartbeat(self.poll_interval_minutes() * 60)

        await asyncio.to_thread(self.storage.write_heartbeat, "stopped", "shut down cleanly")
        log.info("ingestion daemon stopped")

    async def _sleep_with_heartbeat(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self.stopping:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=min(HEARTBEAT_SECONDS, remaining)
                )
                return  # stop was requested
            except asyncio.TimeoutError:
                pass
            status = self.storage.get_setting("daemon_status", "active") or "active"
            await asyncio.to_thread(
                self.storage.write_heartbeat,
                status,
                f"idle, next cycle in {max(0, int(deadline - time.monotonic()))}s",
            )


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
