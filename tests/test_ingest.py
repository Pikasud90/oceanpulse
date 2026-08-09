"""Ingestion: rate limiting, error classification, parsing, archive floors."""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from oceanpulse.ingest.cache_ledger import (
    CachedRange,
    chunk_date_range,
    contains,
    is_covered,
)
from oceanpulse.ingest.grid import OceanMask, build_macro_grid, equal_area_points
from oceanpulse.ingest.http import TokenBucket
from oceanpulse.ingest.noaa_erddap import (
    _parse_griddap_csv,
    _parse_time_actual_range,
    _rows_to_observations,
)
from oceanpulse.ingest.open_meteo import (
    _as_locations,
    _iso_to_ms,
    _parse_location,
    archive_floor,
    clamp_start_date,
    variables_available_from,
)
from oceanpulse.storage.base import BoundingBox
from tests.conftest import (
    ERDDAP_INFO_CSV,
    ERDDAP_SLA_CSV,
    ERDDAP_SST_CSV,
    OPEN_METEO_BATCH,
)

NOW_MS = 1_786_000_000_000


# -- rate limiting ---------------------------------------------------------


def test_token_bucket_limits_sustained_rate():
    async def drain():
        bucket = TokenBucket(rate_per_second=10.0, burst=2)
        started = time.monotonic()
        for _ in range(8):
            await bucket.acquire()
        return time.monotonic() - started

    elapsed = asyncio.run(drain())
    # Two are free from the burst; the remaining six take ~0.6s at 10/s.
    assert elapsed >= 0.5


def test_token_bucket_allows_the_burst_immediately():
    async def burst():
        bucket = TokenBucket(rate_per_second=1.0, burst=4)
        started = time.monotonic()
        for _ in range(4):
            await bucket.acquire()
        return time.monotonic() - started

    assert asyncio.run(burst()) < 0.2


# -- Open-Meteo parsing ----------------------------------------------------


def test_single_location_response_is_normalised():
    """One coordinate returns an object; many return an array."""
    assert len(_as_locations(OPEN_METEO_BATCH)) == 2
    assert len(_as_locations(OPEN_METEO_BATCH[0])) == 1
    assert _as_locations(None) == []


def test_ocean_location_parses():
    observations, had_data = _parse_location(
        OPEN_METEO_BATCH[0],
        requested_lat=30.0,
        requested_lon=-40.0,
        port_id=None,
        now_ms=NOW_MS,
        is_historical=False,
    )
    assert had_data
    assert len(observations) == 3
    assert observations[0].wave_height_m == 1.18
    assert observations[0].sst_celsius == 28.6
    # The snapped cell centre is what the values describe, not what we asked.
    assert observations[0].latitude == pytest.approx(30.041664)


def test_land_location_yields_nothing_and_is_flagged():
    """The all-null payload is the only signal that a cell is dry.

    HTTP status is 200, the JSON is well formed, and every value is null.
    """
    observations, had_data = _parse_location(
        OPEN_METEO_BATCH[1],
        requested_lat=28.6,
        requested_lon=77.2,
        port_id=None,
        now_ms=NOW_MS,
        is_historical=False,
    )
    assert observations == []
    assert had_data is False


def test_future_hours_are_marked_as_forecast():
    observations, _ = _parse_location(
        OPEN_METEO_BATCH[0],
        requested_lat=30.0,
        requested_lon=-40.0,
        port_id=None,
        now_ms=0,  # everything is in the future relative to the epoch
        is_historical=False,
    )
    assert all(o.is_forecast for o in observations)


def test_iso_times_are_treated_as_utc():
    """Open-Meteo returns naive local times; we request timezone=GMT."""
    expected = int(dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert _iso_to_ms("2026-08-09T00:00") == expected
    assert _iso_to_ms("2026-08-09T00:00Z") == expected
    # A naive string must never be read in the machine's local zone.
    assert _iso_to_ms("2026-08-09T13:00") - expected == 13 * 3_600_000


# -- archive floors --------------------------------------------------------


def test_archive_floors_are_enforced():
    """Requesting 1979 returns HTTP 200 and a wall of nulls, not an error."""
    assert clamp_start_date(dt.date(1979, 1, 1)) == archive_floor()
    assert clamp_start_date(dt.date(2025, 1, 1)) == dt.date(2025, 1, 1)


def test_variables_have_different_floors():
    """Waves reach back to 2021; sea-surface temperature only to 2022-12."""
    early = variables_available_from(dt.date(2022, 1, 15))
    assert early["wave_height"] is True
    assert early["sea_surface_temperature"] is False

    later = variables_available_from(dt.date(2023, 6, 1))
    assert all(later.values())


# -- ERDDAP parsing --------------------------------------------------------


def test_griddap_csv_skips_the_units_row():
    """Header, then units, then data.

    Forgetting the units row shifts every value by one and produces a
    plausible-looking but entirely wrong first record.
    """
    rows = _parse_griddap_csv(ERDDAP_SST_CSV)
    assert len(rows) == 3
    assert rows[0]["sst"] == "22.07"
    assert rows[0]["time"] == "2023-01-01T12:00:00Z"


def test_griddap_error_document_yields_no_rows():
    error = 'Error {\n  code=404;\n  message="Not Found: Currently unknown datasetID=x";\n}'
    assert _parse_griddap_csv(error) == []
    assert _parse_griddap_csv("") == []


def test_erddap_nan_becomes_a_missing_value():
    observations = _rows_to_observations(
        _parse_griddap_csv(ERDDAP_SST_CSV), port_id=None, source_kind="sst"
    )
    # Three CSV rows, but the NaN one carries no measurement at all.
    assert len(observations) == 2
    assert observations[0].sst_celsius == 22.07
    assert observations[0].is_historical_cache is True


def test_erddap_sla_has_no_zlev_dimension():
    """Dimension signatures differ between datasets and cannot be assumed."""
    rows = _parse_griddap_csv(ERDDAP_SLA_CSV)
    assert "zlev" not in rows[0]
    observations = _rows_to_observations(rows, port_id="wpi:1", source_kind="sla")
    assert len(observations) == 2
    assert observations[0].sea_level_anomaly_m == 0.0085
    assert observations[0].port_id == "wpi:1"


def test_resolve_cell_searches_outwards_when_masked():
    """One product's water is another's land.

    Open-Meteo resolves a spot just off Rotterdam on its own wave grid; OISST
    has that same 0.25 degree cell masked and returns NaN for every day. Without
    an outward search, sea temperature is permanently empty for exactly the
    coastal ports this application exists to examine.
    """
    from oceanpulse.ingest.noaa_erddap import ErddapClient

    client = ErddapClient(client=None)  # no HTTP: _fetch_chunk is replaced
    working = (51.9, 3.98)
    calls: list[tuple[float, float]] = []

    async def fake_chunk(kind, lat, lon, start, end):
        calls.append((round(lat, 2), round(lon, 2)))
        if (round(lat, 2), round(lon, 2)) == working:
            return _parse_griddap_csv(ERDDAP_SST_CSV)
        return []

    client._fetch_chunk = fake_chunk  # type: ignore[assignment]
    found = asyncio.run(client.resolve_cell("sst", 51.9, 4.23, dt.date(2026, 3, 1)))

    assert found == working
    assert calls[0] == (51.9, 4.23)  # the requested point is tried first
    # And the answer is cached rather than re-probed.
    before = len(calls)
    asyncio.run(client.resolve_cell("sst", 51.9, 4.23, dt.date(2026, 3, 1)))
    assert len(calls) == before


def test_resolve_cell_gives_up_rather_than_wandering():
    from oceanpulse.ingest.noaa_erddap import ErddapClient

    client = ErddapClient(client=None)

    async def always_empty(kind, lat, lon, start, end):
        return []

    client._fetch_chunk = always_empty  # type: ignore[assignment]
    assert asyncio.run(client.resolve_cell("sst", 0.0, 0.0, dt.date(2026, 3, 1))) is None


def test_time_actual_range_parsing():
    bounds = _parse_time_actual_range(ERDDAP_INFO_CSV)
    assert bounds is not None
    assert bounds[0].date() == dt.date(1981, 9, 1)
    assert bounds[1].year == 2026


# -- cache ledger arithmetic -----------------------------------------------


def test_chunking_windows_do_not_overlap():
    """A shared boundary would refetch a day at every chunk edge."""
    windows = chunk_date_range(dt.date(2020, 1, 1), dt.date(2023, 1, 1), chunk_days=365)
    assert windows[0][0] == dt.date(2020, 1, 1)
    assert windows[-1][1] == dt.date(2023, 1, 1)
    for earlier, later in zip(windows, windows[1:]):
        assert later[0] == earlier[1] + dt.timedelta(days=1)


def test_chunking_handles_a_reversed_range():
    assert chunk_date_range(dt.date(2024, 1, 2), dt.date(2024, 1, 1)) == []


def test_containment_not_proximity():
    cached = CachedRange("sst", BoundingBox(29.0, 31.0, -41.0, -39.0), 1000, 2000)
    # A nearby but smaller cached box does not satisfy a wider request.
    assert not contains(cached, "sst", BoundingBox(20.0, 40.0, -50.0, -30.0), 1000, 2000)
    assert contains(cached, "sst", BoundingBox(29.5, 30.5, -40.5, -39.5), 1200, 1800)
    assert not contains(cached, "sla", BoundingBox(29.5, 30.5, -40.5, -39.5), 1200, 1800)


def test_is_covered_scans_all_entries():
    ranges = [
        CachedRange("sst", BoundingBox(0.0, 1.0, 0.0, 1.0), 0, 100),
        CachedRange("sst", BoundingBox(20.0, 40.0, -50.0, -30.0), 0, 5000),
    ]
    assert is_covered(ranges, "sst", BoundingBox(29.0, 31.0, -41.0, -39.0), 100, 4000)
    assert not is_covered(ranges, "sst", BoundingBox(29.0, 31.0, -41.0, -39.0), 100, 9000)


# -- grid and mask ---------------------------------------------------------


def test_equal_area_grid_thins_towards_the_poles():
    """A naive lat/lon grid oversamples the poles by a factor of sixty."""
    points = equal_area_points(10.0, max_latitude=80.0)
    equatorial = sum(1 for lat, _ in points if abs(lat) < 10)
    polar = sum(1 for lat, _ in points if abs(lat) > 70)
    assert equatorial > polar * 2


def test_macro_grid_hits_the_target_size():
    mask = OceanMask()
    for lat in range(-80, 81):
        for lon in range(-180, 180):
            mask.set_ocean(lat + 0.5, lon + 0.5)
    grid = build_macro_grid(mask, target_points=250)
    assert 200 <= len(grid) <= 300


def test_mask_round_trips_through_disk(tmp_path):
    mask = OceanMask()
    mask.set_ocean(30.5, -40.5)
    mask.set_ocean(-45.5, 170.5)
    path = tmp_path / "mask.bin"
    mask.save(path)

    loaded = OceanMask.load(path)
    assert loaded is not None
    assert loaded.is_ocean(30.5, -40.5)
    assert loaded.is_ocean(-45.5, 170.5)
    assert not loaded.is_ocean(0.5, 0.5)
    assert loaded.ocean_cell_count() == 2


def test_corrupt_mask_is_ignored_rather_than_crashing(tmp_path):
    path = tmp_path / "mask.bin"
    path.write_bytes(b"nonsense")
    assert OceanMask.load(path) is None
    assert OceanMask.load(tmp_path / "absent.bin") is None


def test_nearest_ocean_cell_searches_outwards():
    mask = OceanMask()
    mask.set_ocean(10.5, 20.5)
    # A harbour coordinate two cells inland still resolves to open water.
    found = mask.nearest_ocean_cell(12.5, 20.5, max_rings=4)
    assert found is not None
    assert found[0] == pytest.approx(10.5, abs=1.0)


def test_mask_wraps_at_the_antimeridian():
    mask = OceanMask()
    mask.set_ocean(0.5, 179.5)
    assert mask.is_ocean(0.5, 179.5)
    assert mask.is_ocean(0.5, -180.5)  # same cell, expressed the other way
