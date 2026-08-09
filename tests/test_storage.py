"""Storage: source merging, containment, geography."""

from __future__ import annotations

import pytest

from oceanpulse.models import MarineObservation
from oceanpulse.storage.base import BoundingBox, ObservationFilter

BASE_MS = 1_700_000_000_000


def test_sources_merge_onto_one_row(storage):
    """Waves and sea level for the same cell and hour are one observation."""
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.0, sst_celsius=21.5)],
        source="open_meteo",
    )
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           sea_level_anomaly_m=0.08)],
        source="erddap_sla",
    )
    rows = storage.query_observations(ObservationFilter())
    assert len(rows) == 1
    assert rows[0]["wave_height_m"] == 2.0
    assert rows[0]["sea_level_anomaly_m"] == 0.08
    assert "open_meteo" in rows[0]["sources"] and "erddap_sla" in rows[0]["sources"]


def test_daily_sst_never_overwrites_hourly_sst(storage):
    """ERDDAP OISST is daily; Open-Meteo is hourly. Precedence is explicit."""
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           sst_celsius=21.5)],
        source="open_meteo",
    )
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           sst_celsius=99.9)],
        source="erddap_sst",
    )
    rows = storage.query_observations(ObservationFilter())
    assert rows[0]["sst_celsius"] == 21.5


def test_erddap_sst_fills_a_genuine_gap(storage):
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.0)],
        source="open_meteo",
    )
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           sst_celsius=19.4)],
        source="erddap_sst",
    )
    rows = storage.query_observations(ObservationFilter())
    assert rows[0]["sst_celsius"] == 19.4


def test_owning_source_may_correct_its_own_value(storage):
    """A re-polled forecast hour must accept the revised number."""
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.0, is_forecast=True)],
        source="open_meteo",
    )
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.4, is_forecast=False)],
        source="open_meteo",
    )
    rows = storage.query_observations(ObservationFilter())
    assert rows[0]["wave_height_m"] == 2.4
    assert rows[0]["is_forecast"] == 0


def test_forecast_flag_never_regresses(storage):
    """Once an hour is an analysis it cannot become a forecast again."""
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.0, is_forecast=False)],
        source="open_meteo",
    )
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.1, is_forecast=True)],
        source="open_meteo",
    )
    rows = storage.query_observations(ObservationFilter(include_forecast=True))
    assert rows[0]["is_forecast"] == 0


def test_historical_flag_latches_on(storage):
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.0, is_historical_cache=True)],
        source="open_meteo",
    )
    storage.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0, timestamp=BASE_MS,
                           wave_height_m=2.0, is_historical_cache=False)],
        source="open_meteo",
    )
    rows = storage.query_observations(ObservationFilter())
    assert rows[0]["is_historical_cache"] == 1


def test_forecasts_are_excluded_by_default(storage, hourly_series):
    storage.upsert_observations(hourly_series, source="open_meteo")
    storage.upsert_observations(
        [MarineObservation(latitude=31.0, longitude=-41.0, timestamp=BASE_MS,
                           wave_height_m=3.0, is_forecast=True)],
        source="open_meteo",
    )
    assert len(storage.query_observations(ObservationFilter())) == 12
    assert len(storage.query_observations(ObservationFilter(include_forecast=True))) == 13


def test_upsert_is_idempotent(storage, hourly_series):
    storage.upsert_observations(hourly_series, source="open_meteo")
    storage.upsert_observations(hourly_series, source="open_meteo")
    assert len(storage.query_observations(ObservationFilter())) == 12


# -- geography -------------------------------------------------------------


def test_radius_query_uses_true_distance(storage):
    """A bounding box prefilter must not leak the corners into the result."""
    storage.upsert_observations(
        [
            MarineObservation(latitude=0.0, longitude=0.0, timestamp=BASE_MS,
                              wave_height_m=1.0),
            # ~157 km away on the diagonal: inside the box, outside the circle.
            MarineObservation(latitude=1.0, longitude=1.0, timestamp=BASE_MS,
                              wave_height_m=1.0),
        ],
        source="open_meteo",
    )
    rows = storage.query_observations(
        ObservationFilter(centre=(0.0, 0.0), radius_km=120.0)
    )
    assert len(rows) == 1
    assert rows[0]["latitude"] == 0.0


def test_radius_query_wraps_the_antimeridian(storage):
    storage.upsert_observations(
        [
            MarineObservation(latitude=0.0, longitude=179.5, timestamp=BASE_MS,
                              wave_height_m=1.0),
            MarineObservation(latitude=0.0, longitude=-179.5, timestamp=BASE_MS,
                              wave_height_m=1.0),
        ],
        source="open_meteo",
    )
    rows = storage.query_observations(
        ObservationFilter(centre=(0.0, 180.0), radius_km=200.0)
    )
    assert len(rows) == 2


def test_bbox_filter(storage):
    storage.upsert_observations(
        [
            MarineObservation(latitude=10.0, longitude=10.0, timestamp=BASE_MS,
                              wave_height_m=1.0),
            MarineObservation(latitude=50.0, longitude=50.0, timestamp=BASE_MS,
                              wave_height_m=1.0),
        ],
        source="open_meteo",
    )
    rows = storage.query_observations(
        ObservationFilter(bbox=BoundingBox(0.0, 20.0, 0.0, 20.0))
    )
    assert len(rows) == 1


# -- cache ledger ----------------------------------------------------------


def test_ledger_requires_containment_not_overlap(storage):
    """A small cached box must never satisfy a large request.

    This is the silent-truncation bug: the user asks for a wide area, a narrow
    cached entry reports a hit, and they are served a fraction of the data
    believing it complete.
    """
    small = BoundingBox(29.0, 31.0, -41.0, -39.0)
    large = BoundingBox(20.0, 40.0, -50.0, -30.0)
    storage.record_fetch("erddap_sst", small, BASE_MS, BASE_MS + 1000)
    assert not storage.is_range_cached("erddap_sst", large, BASE_MS, BASE_MS + 1000)

    storage.record_fetch("erddap_sst", large, BASE_MS - 10_000, BASE_MS + 10_000)
    assert storage.is_range_cached("erddap_sst", small, BASE_MS, BASE_MS + 1000)


def test_ledger_is_per_dataset(storage):
    """SST coverage says nothing about sea level coverage."""
    box = BoundingBox(29.0, 31.0, -41.0, -39.0)
    storage.record_fetch("erddap_sst", box, BASE_MS, BASE_MS + 1000)
    assert storage.is_range_cached("erddap_sst", box, BASE_MS, BASE_MS + 1000)
    assert not storage.is_range_cached("erddap_sla", box, BASE_MS, BASE_MS + 1000)


def test_ledger_rejects_a_longer_period(storage):
    box = BoundingBox(29.0, 31.0, -41.0, -39.0)
    storage.record_fetch("erddap_sst", box, BASE_MS, BASE_MS + 1000)
    assert not storage.is_range_cached("erddap_sst", box, BASE_MS, BASE_MS + 999_999)


# -- ancillary tables ------------------------------------------------------


def test_tracked_port_round_trip(storage):
    storage.add_tracked_port(
        {
            "port_id": "wpi:1",
            "port_name": "Testport",
            "country_code": "XX",
            "latitude": 10.0,
            "longitude": 20.0,
            "marine_latitude": 10.25,
            "marine_longitude": 20.25,
        }
    )
    ports = storage.get_tracked_ports()
    assert len(ports) == 1 and ports[0]["marine_latitude"] == 10.25

    # A later write without a resolved cell must not erase the one we have.
    storage.add_tracked_port(
        {"port_id": "wpi:1", "port_name": "Testport", "latitude": 10.0, "longitude": 20.0}
    )
    assert storage.get_tracked_port("wpi:1")["marine_latitude"] == 10.25


def test_saved_dataset_round_trip(storage):
    spec = {"scope_mode": "global", "interval": "1d", "start": "2024-01-01"}
    storage.save_dataset("my dataset", spec)
    assert storage.get_saved_dataset("my dataset")["spec"] == spec
    storage.save_dataset("my dataset", {**spec, "interval": "1h"})
    assert len(storage.list_saved_datasets()) == 1
    assert storage.get_saved_dataset("my dataset")["spec"]["interval"] == "1h"
    storage.delete_saved_dataset("my dataset")
    assert storage.list_saved_datasets() == []


def test_daemon_health_reports_stopped_when_stale(storage):
    assert storage.daemon_health()["status"] == "stopped"
    storage.write_heartbeat("active", "polling")
    assert storage.daemon_health()["status"] == "active"
    assert storage.daemon_health(stale_after_seconds=-1)["status"] == "stopped"
    storage.write_heartbeat("degraded", "network error")
    assert storage.daemon_health()["status"] == "degraded"


def test_grid_probe_marks_validity(storage):
    storage.upsert_grid_points([(10.0, 20.0, True), (30.0, 40.0, True)])
    points = storage.get_grid_points()
    assert len(points) == 2
    storage.mark_grid_probe([points[0]["grid_id"]], [points[1]["grid_id"]])
    valid = storage.get_grid_points(only_valid=True)
    assert len(valid) == 1
