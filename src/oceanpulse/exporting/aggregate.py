"""Turn stored observations into the uniform matrix a model expects.

Two decisions here matter more than the mechanics.

**Gap filling is per column, not global.** The obvious design is one "fill with
zero or leave blank" switch applied to every column. Applied uniformly it
corrupts the data, because zero means different things in different columns:

    extensive   observation_count, wave energy   zero is the TRUE value
    intensive   temperature, wave height, SLA    zero is a measurement of 0

Filling `sst_celsius` with zero does not say "no reading"; it asserts that the
sea was at freezing point. So counts and summed energy always fill with zero,
and the user's choice applies only where it is a genuine modelling decision.

**Timestamps stay unambiguous.** Everything is stored as integer UTC epoch
milliseconds and written to Parquet through an explicit timezone-aware
`pyarrow` schema, so a dataloader reading the file back gets the same instant
without re-parsing or guessing a local zone.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Any, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..logging_setup import get_logger
from ..math_engine import (
    INTERVAL_RULES,
    resample_observations,
    rolling_zscore,
    wave_energy_flux_series,
)
from ..storage.base import BoundingBox, ObservationFilter
from ..storage.sqlite_backend import SQLiteStorage

log = get_logger(__name__)

RAW_COLUMNS = [
    "time",
    "latitude",
    "longitude",
    "port_id",
    "wave_height_m",
    "wave_period_s",
    "wave_direction_deg",
    "wave_power_kw_m",
    "current_velocity_kmh",
    "current_direction_deg",
    "sst_celsius",
    "sea_level_anomaly_m",
    "geostrophic_u_ms",
    "geostrophic_v_ms",
    "is_forecast",
    "sources",
]

FILL_MODES = ("none", "zero", "ffill", "interpolate")


def observations_to_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Rows from storage into a typed frame, with wave power derived."""
    if not rows:
        return pd.DataFrame(columns=RAW_COLUMNS)

    frame = pd.DataFrame(list(rows))
    frame["time"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame["wave_power_kw_m"] = wave_energy_flux_series(
        frame.get("wave_height_m", pd.Series(dtype=float)),
        frame.get("wave_period_s", pd.Series(dtype=float)),
    )
    for column in RAW_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame.sort_values("time").reset_index(drop=True)


def build_dataset(
    storage: SQLiteStorage,
    *,
    start: dt.datetime,
    end: dt.datetime,
    bbox: BoundingBox | None = None,
    port_id: str | None = None,
    centre: tuple[float, float] | None = None,
    radius_km: float | None = None,
    mode: str = "aggregated",
    interval: str = "1d",
    intensive_fill: str = "none",
    include_forecast: bool = False,
    derived_features: bool = False,
    max_rows: int = 2_000_000,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Assemble a dataset and report how it was built."""
    filters = ObservationFilter(
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
        bbox=bbox,
        port_id=port_id,
        centre=centre,
        radius_km=radius_km,
        include_forecast=include_forecast,
        limit=max_rows,
        order="asc",
    )
    rows = storage.query_observations(filters)
    raw = observations_to_frame(rows)

    meta: dict[str, Any] = {
        "matched_observations": len(raw),
        "mode": mode,
        "interval": interval,
        "intensive_fill": intensive_fill,
        "include_forecast": include_forecast,
        "truncated": len(rows) >= max_rows,
    }

    if mode == "raw":
        frame = raw[RAW_COLUMNS].copy()
        meta["rows"] = len(frame)
        return frame, meta

    if interval not in INTERVAL_RULES:
        interval = "1d"
    frame = resample_observations(
        raw,
        interval=interval,
        start=pd.Timestamp(start).tz_convert("UTC")
        if pd.Timestamp(start).tzinfo
        else pd.Timestamp(start, tz="UTC"),
        end=pd.Timestamp(end).tz_convert("UTC")
        if pd.Timestamp(end).tzinfo
        else pd.Timestamp(end, tz="UTC"),
        intensive_fill=intensive_fill,
    )

    if not frame.empty:
        # Running totals are convenient features and cheap to add here.
        if "observation_count" in frame.columns:
            frame["cumulative_observations"] = frame["observation_count"].cumsum()
        if "wave_energy_kwh_m" in frame.columns:
            frame["cumulative_wave_energy_kwh_m"] = (
                frame["wave_energy_kwh_m"].fillna(0.0).cumsum()
            )
        if derived_features:
            frame = add_derived_features(frame)

    meta["rows"] = len(frame)
    meta["derived_features"] = bool(derived_features)
    return frame, meta


# Columns worth a standardised anomaly: the ones whose *departure* from normal
# is the interesting signal rather than their absolute value.
ANOMALY_COLUMNS = ("sst_celsius", "sea_level_anomaly_m", "wave_power_kw_m", "wave_height_m")


def add_derived_features(frame: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """Add rolling z-scores and a log-energy column for modelling.

    Offered as an option rather than always-on because these are opinionated
    transforms: the window length is a modelling choice, and a reader who wants
    the raw series should not have to strip columns back out.
    """
    out = frame.copy()
    for column in ANOMALY_COLUMNS:
        if column in out.columns and out[column].notna().sum() >= 8:
            out[f"{column}_z"] = rolling_zscore(
                out[column].tolist(), window=window, min_periods=5
            )
    if "wave_energy_kwh_m" in out.columns:
        # log1p keeps the zeros that quiet windows legitimately contain.
        out["log1p_wave_energy_kwh_m"] = np.log1p(
            out["wave_energy_kwh_m"].clip(lower=0).fillna(0.0)
        )
    return out


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    """CSV with explicit UTC ISO-8601 timestamps.

    CSV has no type system, so the timezone has to survive as text or it does
    not survive at all.
    """
    output = frame.copy()
    for column in output.columns:
        if pd.api.types.is_datetime64_any_dtype(output[column]):
            output[column] = output[column].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    buffer = io.StringIO()
    output.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")


def to_parquet_bytes(frame: pd.DataFrame) -> bytes:
    """Parquet with an explicit timezone-aware timestamp schema."""
    output = frame.copy()
    fields = []
    for column in output.columns:
        series = output[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            if series.dt.tz is None:
                output[column] = series.dt.tz_localize("UTC")
            fields.append(pa.field(column, pa.timestamp("us", tz="UTC")))
        elif pd.api.types.is_integer_dtype(series):
            fields.append(pa.field(column, pa.int64()))
        elif pd.api.types.is_bool_dtype(series):
            fields.append(pa.field(column, pa.bool_()))
        elif pd.api.types.is_float_dtype(series):
            fields.append(pa.field(column, pa.float64()))
        else:
            output[column] = series.astype("string")
            fields.append(pa.field(column, pa.string()))

    table = pa.Table.from_pandas(
        output, schema=pa.schema(fields), preserve_index=False
    )
    buffer = pa.BufferOutputStream()
    pq.write_table(table, buffer, compression="snappy")
    return buffer.getvalue().to_pybytes()


def suggest_filename(prefix: str, extension: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in prefix)[:48]
    return f"oceanpulse_{safe}_{stamp}.{extension}"
