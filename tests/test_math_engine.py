"""Physics, statistics and downsampling."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oceanpulse.math_engine import (
    WAVE_POWER_COEFFICIENT,
    circular_mean_deg,
    correlation_matrix,
    downsample_lttb,
    haversine_km,
    heatwave_days,
    resample_observations,
    rolling_zscore,
    wave_energy_flux_kw_m,
)


def test_wave_power_matches_closed_form():
    # P = rho g^2 / (64 pi) * Hm0^2 * T, i.e. about 0.49 * H^2 * T kW/m.
    assert WAVE_POWER_COEFFICIENT / 1000.0 == pytest.approx(0.4906, abs=1e-3)
    assert wave_energy_flux_kw_m(2.0, 8.0) == pytest.approx(15.7, abs=0.1)
    assert wave_energy_flux_kw_m(1.0, 10.0) == pytest.approx(4.91, abs=0.05)


def test_wave_power_missing_input_is_none_not_zero():
    """An unmeasured sea state is not a calm one."""
    assert wave_energy_flux_kw_m(None, 8.0) is None
    assert wave_energy_flux_kw_m(2.0, None) is None
    assert wave_energy_flux_kw_m(2.0, 0.0) is None


def test_circular_mean_crosses_north():
    """Averaging 350 and 10 arithmetically gives 180 — due south."""
    assert circular_mean_deg([350.0, 10.0]) == pytest.approx(0.0, abs=1e-6)
    assert circular_mean_deg([90.0, 110.0]) == pytest.approx(100.0, abs=1e-6)
    assert circular_mean_deg([]) is None
    assert circular_mean_deg([None, None]) is None


def test_haversine_known_distance():
    # Gibraltar to Tangier, about 58 km.
    assert haversine_km(36.14, -5.35, 35.78, -5.81) == pytest.approx(57.6, abs=1.0)
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(20015.0, abs=5.0)


def test_lttb_preserves_endpoints_and_peaks():
    x = np.arange(10_000.0)
    y = np.sin(x / 100.0)
    y[5000] = 99.0  # a spike naive stride sampling would usually miss
    sx, sy = downsample_lttb(x, y, 2500)
    assert len(sx) == 2500
    assert sx[0] == x[0] and sx[-1] == x[-1]
    assert 99.0 in sy


def test_lttb_leaves_short_series_alone():
    x = np.arange(10.0)
    sx, sy = downsample_lttb(x, x * 2, 2500)
    assert len(sx) == 10


def test_lttb_drops_nan_before_bucketing():
    x = np.arange(100.0)
    y = np.arange(100.0)
    y[10:20] = np.nan
    sx, sy = downsample_lttb(x, y, 50)
    assert np.isfinite(sy).all()


def test_rolling_zscore_excludes_the_current_point():
    values = [10.0] * 20 + [20.0]
    z = rolling_zscore(values, window=10, min_periods=5)
    # A constant baseline has zero variance, so the jump is undefined rather
    # than infinite — and must not be a division by zero.
    assert not np.isfinite(z[-1])

    noisy = list(np.random.RandomState(0).normal(10.0, 1.0, 40)) + [20.0]
    z2 = rolling_zscore(noisy, window=30, min_periods=10)
    assert z2[-1] > 3.0


def test_heatwave_requires_minimum_duration():
    dates = pd.date_range("2024-01-01", periods=200, freq="1D", tz="UTC")
    sst = np.full(200, 20.0)
    sst[100:102] = 30.0  # two days: too short to count
    short = heatwave_days(dates, sst, baseline_window=60, min_duration=5)
    assert not short[100:102].any()

    sst2 = np.full(200, 20.0)
    sst2[100:110] = 30.0  # ten days: a spell
    long = heatwave_days(dates, sst2, baseline_window=60, min_duration=5)
    assert long[100:110].any()


def test_correlation_drops_thin_columns():
    frame = pd.DataFrame(
        {
            "a": [1.0, 2.0, 3.0, 4.0],
            "b": [2.0, 4.0, 6.0, 8.0],
            "c": [1.0, None, None, None],
        }
    )
    matrix = correlation_matrix(frame, ["a", "b", "c"])
    assert "c" not in matrix.columns
    assert matrix.loc["a", "b"] == pytest.approx(1.0)


def _frame(hours: int = 48) -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=hours, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "time": times,
            "sst_celsius": np.linspace(18.0, 22.0, hours),
            "wave_height_m": np.linspace(1.0, 3.0, hours),
            "wave_power_kw_m": np.linspace(5.0, 25.0, hours),
            "wave_direction_deg": np.linspace(0.0, 359.0, hours),
        }
    )


def test_resample_pins_grid_to_requested_range():
    """Quiet periods at the edges are represented, not trimmed away."""
    frame = _frame(24)
    out = resample_observations(
        frame,
        interval="1d",
        start=pd.Timestamp("2023-12-28", tz="UTC"),
        end=pd.Timestamp("2024-01-05", tz="UTC"),
    )
    assert out["window_start"].min() <= pd.Timestamp("2023-12-28", tz="UTC")
    assert out["window_start"].max() >= pd.Timestamp("2024-01-05", tz="UTC")


def test_counts_always_zero_fill_regardless_of_choice():
    """Zero is the true value for a count, whatever the user picked."""
    frame = _frame(24)
    for mode in ("none", "ffill", "interpolate", "zero"):
        out = resample_observations(
            frame,
            interval="1d",
            start=pd.Timestamp("2023-12-28", tz="UTC"),
            end=pd.Timestamp("2024-01-05", tz="UTC"),
            intensive_fill=mode,
        )
        empty = out[out["window_start"] < pd.Timestamp("2024-01-01", tz="UTC")]
        assert (empty["observation_count"] == 0).all()


def test_intensive_columns_are_not_zero_filled_by_default():
    """Zero degrees is a measurement, not a gap."""
    frame = _frame(24)
    out = resample_observations(
        frame,
        interval="1d",
        start=pd.Timestamp("2023-12-28", tz="UTC"),
        end=pd.Timestamp("2024-01-05", tz="UTC"),
        intensive_fill="none",
    )
    empty = out[out["window_start"] < pd.Timestamp("2024-01-01", tz="UTC")]
    assert empty["sst_celsius"].isna().all()

    filled = resample_observations(
        frame,
        interval="1d",
        start=pd.Timestamp("2023-12-28", tz="UTC"),
        end=pd.Timestamp("2024-01-05", tz="UTC"),
        intensive_fill="zero",
    )
    empty_filled = filled[filled["window_start"] < pd.Timestamp("2024-01-01", tz="UTC")]
    assert (empty_filled["sst_celsius"] == 0.0).all()


def test_direction_aggregation_uses_circular_mean():
    times = pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {"time": times, "wave_direction_deg": [350.0, 10.0], "sst_celsius": [20.0, 20.0]}
    )
    out = resample_observations(
        frame,
        interval="1d",
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-01", tz="UTC"),
    )
    bearing = out["wave_direction_deg"].dropna().iloc[0]
    assert bearing == pytest.approx(0.0, abs=1e-6) or bearing == pytest.approx(360.0, abs=1e-6)
