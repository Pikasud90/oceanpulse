"""The validation boundary."""

from __future__ import annotations

import pytest

from oceanpulse.models import MarineObservation, observation_id, wrap_longitude

BASE_MS = 1_700_000_000_000


def test_all_null_observation_is_rejected():
    """The single most important rule in the project.

    Open-Meteo answers HTTP 200 for a land coordinate and for the year 1979
    alike. Only the all-null payload distinguishes them from real data, so an
    empty observation must never reach the database.
    """
    with pytest.raises(ValueError):
        MarineObservation(latitude=10.0, longitude=20.0, timestamp=BASE_MS)


def test_partial_observation_is_accepted():
    obs = MarineObservation(
        latitude=10.0, longitude=20.0, timestamp=BASE_MS, sst_celsius=18.2
    )
    assert obs.sst_celsius == 18.2
    assert obs.wave_height_m is None


def test_longitude_is_wrapped_not_rejected():
    """0-360 datasets legitimately produce longitudes above 180."""
    obs = MarineObservation(
        latitude=10.0, longitude=200.0, timestamp=BASE_MS, wave_height_m=1.0
    )
    assert obs.longitude == pytest.approx(-160.0)


def test_latitude_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        MarineObservation(
            latitude=91.0, longitude=0.0, timestamp=BASE_MS, wave_height_m=1.0
        )


def test_seconds_timestamp_is_rejected():
    """A seconds-vs-milliseconds slip would silently land in 1970."""
    MarineObservation(
        latitude=0.0, longitude=0.0, timestamp=BASE_MS, wave_height_m=1.0
    )
    with pytest.raises(ValueError):
        MarineObservation(
            latitude=0.0, longitude=0.0, timestamp=-1, wave_height_m=1.0
        )


def test_nan_becomes_null():
    """ERDDAP writes NaN for masked land cells in CSV output."""
    obs = MarineObservation(
        latitude=0.0,
        longitude=0.0,
        timestamp=BASE_MS,
        sst_celsius=float("nan"),
        wave_height_m=1.0,
    )
    assert obs.sst_celsius is None


def test_bearings_are_normalised():
    obs = MarineObservation(
        latitude=0.0,
        longitude=0.0,
        timestamp=BASE_MS,
        wave_direction_deg=730.0,
        wave_height_m=1.0,
    )
    assert obs.wave_direction_deg == pytest.approx(10.0)


def test_observation_id_ignores_source():
    """Identity is position and time only, so sources merge onto one row."""
    a = observation_id(30.0, -40.0, BASE_MS)
    b = observation_id(30.0, -40.0, BASE_MS)
    c = observation_id(30.0, -40.01, BASE_MS)
    assert a == b
    assert a != c


def test_observation_id_matches_wrapped_longitude():
    assert observation_id(10.0, -160.0, BASE_MS) == observation_id(
        10.0, wrap_longitude(200.0), BASE_MS
    )
