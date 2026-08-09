"""Derived physical quantities, statistics and rendering-side optimisation.

Everything here is pure: it takes numbers in and gives numbers out, touching
neither the network nor the database. That is what makes it straightforward to
test, and the test suite leans on it heavily.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# Seawater density (kg/m^3) and gravity (m/s^2).
SEAWATER_DENSITY = 1025.0
GRAVITY = 9.81

# rho * g^2 / (64 * pi), in W per (m^2 * s). Multiplying by Hm0^2 * T gives
# W/m; divide by 1000 for kW/m. Works out to 490.6, i.e. the familiar 0.49.
WAVE_POWER_COEFFICIENT = SEAWATER_DENSITY * GRAVITY**2 / (64.0 * math.pi)

# Peak period Tp overestimates wave power relative to the energy period Te
# that the deep-water formula actually calls for. For a JONSWAP-like spectrum
# Te ~= 0.9 * Tp, so using Tp inflates the answer by about 11%. Open-Meteo
# publishes a peak period, so that is what we have; the constant is exposed
# rather than buried so the assumption is visible and adjustable.
ENERGY_PERIOD_RATIO = 1.0

EARTH_RADIUS_KM = 6371.0088


# ---------------------------------------------------------------------------
# Wave energy flux
# ---------------------------------------------------------------------------


def wave_energy_flux_kw_m(
    wave_height_m: float | None,
    wave_period_s: float | None,
    energy_period_ratio: float = ENERGY_PERIOD_RATIO,
) -> float | None:
    """Deep-water wave power per metre of crest, in kW/m.

        P = rho * g^2 / (64 * pi) * Hm0^2 * T

    Returns None when either input is missing, rather than 0.0: an unmeasured
    sea state is not a calm one.
    """
    if wave_height_m is None or wave_period_s is None:
        return None
    try:
        height = float(wave_height_m)
        period = float(wave_period_s)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(height) and math.isfinite(period)):
        return None
    if height < 0.0 or period <= 0.0:
        return None
    watts = WAVE_POWER_COEFFICIENT * height**2 * (period * energy_period_ratio)
    return watts / 1000.0


def wave_energy_flux_series(
    heights: Sequence[float | None], periods: Sequence[float | None]
) -> np.ndarray:
    """Vectorised wave power. Missing inputs propagate as NaN."""
    h = pd.to_numeric(pd.Series(list(heights)), errors="coerce").to_numpy(dtype=float)
    t = pd.to_numeric(pd.Series(list(periods)), errors="coerce").to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        power = WAVE_POWER_COEFFICIENT * np.square(h) * t * ENERGY_PERIOD_RATIO / 1000.0
        power = np.where((h < 0) | (t <= 0), np.nan, power)
    return power


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def haversine_km_array(
    lat: float, lon: float, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Distance from one point to many, in kilometres."""
    phi1 = math.radians(lat)
    phi2 = np.radians(lats)
    dphi = phi2 - phi1
    dlambda = np.radians(lons - lon)
    a = np.sin(dphi / 2.0) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))


# ---------------------------------------------------------------------------
# Bearings
# ---------------------------------------------------------------------------


def circular_mean_deg(bearings: Iterable[float | None]) -> float | None:
    """Mean of compass bearings.

    Averaging 350 and 10 arithmetically gives 180 - due south for two headings
    that are both nearly due north. Aggregation of wave and current direction
    has to go through the unit circle.
    """
    values = [float(b) for b in bearings if b is not None and math.isfinite(float(b))]
    if not values:
        return None
    radians = np.radians(np.asarray(values, dtype=float))
    mean = math.degrees(math.atan2(np.sin(radians).mean(), np.cos(radians).mean()))
    mean %= 360.0
    # Floating point turns a true north of 0 into 359.999...; snap it back so
    # the displayed bearing does not read as one degree short of a full turn.
    if mean >= 360.0 - 1e-9:
        mean = 0.0
    return mean


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------


def rolling_zscore(
    values: Sequence[float | None], window: int = 30, min_periods: int = 5
) -> np.ndarray:
    """Standardised anomaly against a trailing baseline.

        Z = (x - mu) / sigma

    The baseline excludes the current point, so a value cannot dampen the very
    statistic used to judge it. Zero-variance windows yield NaN rather than a
    division by zero.
    """
    series = pd.to_numeric(pd.Series(list(values)), errors="coerce")
    shifted = series.shift(1)
    mean = shifted.rolling(window=window, min_periods=min_periods).mean()
    std = shifted.rolling(window=window, min_periods=min_periods).std(ddof=0)
    std = std.where(std > 1e-9)
    return ((series - mean) / std).to_numpy(dtype=float)


def concurrent_anomaly_flags(
    sst_z: Sequence[float], sla_z: Sequence[float], threshold: float = 2.0
) -> np.ndarray:
    """True where sea temperature and sea level are both anomalously high.

    The joint condition is the interesting one: warm water expands, so a
    thermal signal that also shows up in sea level is more likely to be real
    than either alone.
    """
    a = pd.to_numeric(pd.Series(list(sst_z)), errors="coerce").to_numpy(dtype=float)
    b = pd.to_numeric(pd.Series(list(sla_z)), errors="coerce").to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        return (a > threshold) & (b > threshold)


def heatwave_days(
    dates: Sequence,
    sst: Sequence[float | None],
    baseline_window: int = 90,
    percentile: float = 90.0,
    min_duration: int = 5,
) -> np.ndarray:
    """Flag days in a warm-anomaly spell.

    This is a *rolling-baseline approximation*, not the Hobday et al. (2016)
    marine-heatwave definition, which requires a fixed 30-year daily
    climatology that a self-hosted database built over weeks does not have.
    A day qualifies when it exceeds the trailing `baseline_window` day
    `percentile`, and only runs of at least `min_duration` consecutive
    qualifying days are kept.

    The interface labels anything derived from this as an approximation.
    """
    series = pd.to_numeric(pd.Series(list(sst)), errors="coerce")
    if series.notna().sum() < min_duration:
        return np.zeros(len(series), dtype=bool)

    threshold = series.shift(1).rolling(
        window=baseline_window, min_periods=max(10, min_duration * 2)
    ).quantile(percentile / 100.0)
    above = (series > threshold).fillna(False).to_numpy(dtype=bool)

    # Keep only runs long enough to count as a spell.
    out = np.zeros_like(above)
    run_start = None
    for i, flag in enumerate(above):
        if flag and run_start is None:
            run_start = i
        elif not flag and run_start is not None:
            if i - run_start >= min_duration:
                out[run_start:i] = True
            run_start = None
    if run_start is not None and len(above) - run_start >= min_duration:
        out[run_start:] = True
    return out


# ---------------------------------------------------------------------------
# Downsampling
# ---------------------------------------------------------------------------


def downsample_lttb(
    x: Sequence[float], y: Sequence[float], max_points: int = 2500
) -> tuple[np.ndarray, np.ndarray]:
    """Largest-Triangle-Three-Buckets downsampling.

    Preserves the visual shape of a series - peaks and troughs survive - which
    naive stride sampling does not. Points whose y is NaN are dropped first,
    since a triangle area involving NaN is NaN and would swallow a bucket.
    """
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    if xs.size != ys.size:
        raise ValueError("x and y must be the same length")

    finite = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[finite], ys[finite]

    n = xs.size
    if max_points < 3 or n <= max_points:
        return xs, ys

    sampled_x = np.empty(max_points, dtype=float)
    sampled_y = np.empty(max_points, dtype=float)
    sampled_x[0], sampled_y[0] = xs[0], ys[0]
    sampled_x[-1], sampled_y[-1] = xs[-1], ys[-1]

    # Buckets span the interior points; first and last are always kept.
    bucket_size = (n - 2) / (max_points - 2)
    a = 0  # index of the previously selected point

    for i in range(max_points - 2):
        # Average of the *next* bucket forms the third triangle vertex.
        next_start = int(math.floor((i + 1) * bucket_size)) + 1
        next_end = min(int(math.floor((i + 2) * bucket_size)) + 1, n)
        if next_start >= next_end:
            next_start, next_end = min(next_start, n - 1), n
        avg_x = xs[next_start:next_end].mean()
        avg_y = ys[next_start:next_end].mean()

        start = int(math.floor(i * bucket_size)) + 1
        end = min(int(math.floor((i + 1) * bucket_size)) + 1, n - 1)
        if start >= end:
            end = min(start + 1, n - 1)

        seg_x = xs[start:end]
        seg_y = ys[start:end]
        areas = np.abs(
            (xs[a] - avg_x) * (seg_y - ys[a]) - (xs[a] - seg_x) * (avg_y - ys[a])
        )
        chosen = start + int(np.argmax(areas))
        sampled_x[i + 1], sampled_y[i + 1] = xs[chosen], ys[chosen]
        a = chosen

    return sampled_x, sampled_y


def downsample_frame(
    frame: pd.DataFrame, x_column: str, y_column: str, max_points: int = 2500
) -> pd.DataFrame:
    """LTTB applied to a DataFrame, keeping whole rows.

    Selecting rows rather than raw coordinates means hover text and colour
    columns stay aligned with the points that survive.
    """
    if len(frame) <= max_points:
        return frame
    working = frame.dropna(subset=[y_column])
    if len(working) <= max_points:
        return working
    x_numeric = pd.to_numeric(working[x_column], errors="coerce").to_numpy(dtype=float)
    y_numeric = pd.to_numeric(working[y_column], errors="coerce").to_numpy(dtype=float)
    kept_x, _ = downsample_lttb(x_numeric, y_numeric, max_points=max_points)
    positions = np.searchsorted(x_numeric, kept_x)
    positions = np.unique(np.clip(positions, 0, len(working) - 1))
    return working.iloc[positions]


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def correlation_matrix(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Pearson r over the columns that actually contain usable data.

    Columns with fewer than three finite values are dropped: a correlation
    computed from two points is always exactly +/-1 and is meaningless.
    """
    usable = [c for c in columns if c in frame.columns and frame[c].notna().sum() >= 3]
    if len(usable) < 2:
        return pd.DataFrame()
    return frame[usable].corr(method="pearson", min_periods=3)


# ---------------------------------------------------------------------------
# Resampling and gap filling
# ---------------------------------------------------------------------------

INTERVAL_RULES = {
    "1h": "1h",
    "6h": "6h",
    "1d": "1D",
    "1w": "7D",
}

# Extensive quantities sum over a window, so an empty window genuinely is
# zero. Intensive quantities are averages of a physical state; there is no
# measurement at all in an empty window, and zero is a *value*, not a gap.
EXTENSIVE_COLUMNS = ("observation_count", "wave_energy_kwh_m")
INTENSIVE_COLUMNS = (
    "wave_height_m",
    "wave_period_s",
    "current_velocity_kmh",
    "sst_celsius",
    "sea_level_anomaly_m",
    "wave_power_kw_m",
)
DIRECTION_COLUMNS = ("wave_direction_deg", "current_direction_deg")


def resample_observations(
    frame: pd.DataFrame,
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    intensive_fill: str = "none",
) -> pd.DataFrame:
    """Put irregular observations onto a uniform grid.

    The grid is pinned to the *requested* range rather than to the first and
    last observation, so quiet periods at the edges are represented instead of
    trimmed away.

    `intensive_fill` is one of `none`, `zero`, `ffill`, `interpolate`, and
    applies only to intensive columns. Counts and summed energy always fill
    with zero regardless, because for them zero is the true value.
    """
    rule = INTERVAL_RULES.get(interval, "1D")

    grid = pd.date_range(start=start.floor(rule), end=end.ceil(rule), freq=rule, tz="UTC")
    if len(grid) == 0:
        return pd.DataFrame()

    if frame.empty:
        out = pd.DataFrame({"window_start": grid})
    else:
        work = frame.copy()
        work["window_start"] = work["time"].dt.floor(rule)

        aggregations: dict[str, tuple[str, str]] = {}
        for column in INTENSIVE_COLUMNS:
            if column in work.columns:
                aggregations[column] = (column, "mean")
        for column in ("wave_height_m", "wave_power_kw_m"):
            if column in work.columns:
                aggregations[f"{column}_max"] = (column, "max")

        grouped = work.groupby("window_start", sort=True).agg(**aggregations)
        grouped["observation_count"] = work.groupby("window_start").size()

        # Bearings need the unit circle, so they cannot ride along in .agg().
        for column in DIRECTION_COLUMNS:
            if column in work.columns:
                grouped[column] = work.groupby("window_start")[column].apply(
                    lambda s: circular_mean_deg(s.tolist())
                )

        out = grouped.reindex(grid).reset_index(names="window_start")

    if "observation_count" in out.columns:
        out["observation_count"] = out["observation_count"].fillna(0).astype("int64")
    else:
        out["observation_count"] = 0

    # Always emit the full column set, even when nothing matched. Otherwise a
    # query that happens to return no observations produces a narrower frame
    # than the same query over a populated period, and a downstream pipeline
    # that expects fixed columns fails on the empty case only - the hardest
    # kind of break to reproduce.
    for column in (*INTENSIVE_COLUMNS, *DIRECTION_COLUMNS):
        if column not in out.columns:
            out[column] = np.nan
    for column in ("wave_height_m_max", "wave_power_kw_m_max"):
        if column not in out.columns:
            out[column] = np.nan

    # Energy delivered over the window: kW/m averaged, times window hours.
    hours = pd.Timedelta(rule).total_seconds() / 3600.0
    if "wave_power_kw_m" in out.columns:
        out["wave_energy_kwh_m"] = out["wave_power_kw_m"] * hours

    for column in EXTENSIVE_COLUMNS:
        if column in out.columns:
            out[column] = out[column].fillna(0.0)

    intensive_present = [c for c in out.columns if c.startswith(INTENSIVE_COLUMNS)]
    if intensive_fill == "zero":
        out[intensive_present] = out[intensive_present].fillna(0.0)
    elif intensive_fill == "ffill":
        out[intensive_present] = out[intensive_present].ffill()
    elif intensive_fill == "interpolate":
        out[intensive_present] = out[intensive_present].interpolate(
            method="linear", limit_direction="both"
        )

    return out.sort_values("window_start").reset_index(drop=True)
