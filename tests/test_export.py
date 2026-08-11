"""Export: dataset assembly, gap-fill semantics, format fidelity."""

from __future__ import annotations

import datetime as dt
import json
import io

import pandas as pd
import pyarrow.parquet as pq
import pytest

from oceanpulse.exporting.aggregate import (
    build_dataset,
    observations_to_frame,
    suggest_filename,
    to_csv_bytes,
    to_parquet_bytes,
)
from oceanpulse.models import MarineObservation
from oceanpulse.storage.base import BoundingBox, ObservationFilter

BASE_MS = 1_700_000_000_000


@pytest.fixture()
def populated(storage):
    observations = []
    for hour in range(72):
        observations.append(
            MarineObservation(
                latitude=30.0,
                longitude=-40.0,
                timestamp=BASE_MS + hour * 3_600_000,
                port_id="wpi:1",
                wave_height_m=1.0 + (hour % 12) * 0.1,
                wave_period_s=8.0,
                wave_direction_deg=float(hour * 5 % 360),
                sst_celsius=20.0 + (hour % 24) * 0.05,
                sea_level_anomaly_m=0.01 * (hour % 7),
            )
        )
    storage.upsert_observations(observations, source="open_meteo")
    return storage


def _window():
    start = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc)
    return start, start + dt.timedelta(days=3)


def test_wave_power_is_derived_on_read(populated):
    rows = populated.query_observations(ObservationFilter())
    frame = observations_to_frame(rows)
    assert "wave_power_kw_m" in frame.columns
    assert frame["wave_power_kw_m"].notna().any()
    # 0.49 * 1.0^2 * 8.0
    assert frame["wave_power_kw_m"].iloc[0] == pytest.approx(3.92, abs=0.05)


def test_aggregated_dataset_has_uniform_spacing(populated):
    start, end = _window()
    frame, meta = build_dataset(populated, start=start, end=end, mode="aggregated",
                                interval="1d")
    assert meta["mode"] == "aggregated"
    gaps = frame["window_start"].diff().dropna().unique()
    assert len(gaps) == 1


def test_raw_mode_returns_one_row_per_observation(populated):
    start, end = _window()
    frame, meta = build_dataset(populated, start=start, end=end, mode="raw")
    assert meta["rows"] == 72
    assert "time" in frame.columns


def test_quiet_windows_keep_zero_counts_but_empty_temperatures(populated):
    """The gap-fill choice must not reach columns where zero is a value."""
    start = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc) - dt.timedelta(days=5)
    end = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc) + dt.timedelta(days=4)
    frame, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                             interval="1d", intensive_fill="none")
    quiet = frame[frame["observation_count"] == 0]
    assert not quiet.empty
    assert quiet["sst_celsius"].isna().all()


def test_zero_fill_reaches_only_intensive_columns(populated):
    start = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc) - dt.timedelta(days=5)
    end = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc) + dt.timedelta(days=4)
    frame, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                             interval="1d", intensive_fill="zero")
    quiet = frame[frame["observation_count"] == 0]
    assert (quiet["sst_celsius"] == 0.0).all()


def test_forward_fill_carries_the_last_measurement(populated):
    start = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc)
    end = start + dt.timedelta(days=8)
    frame, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                             interval="1d", intensive_fill="ffill")
    tail = frame.tail(3)
    assert tail["sst_celsius"].notna().all()


def test_port_filter_scopes_the_dataset(populated):
    populated.upsert_observations(
        [MarineObservation(latitude=0.0, longitude=0.0, timestamp=BASE_MS,
                           wave_height_m=9.0)],
        source="open_meteo",
    )
    start, end = _window()
    scoped, _ = build_dataset(populated, start=start, end=end, port_id="wpi:1", mode="raw")
    everything, _ = build_dataset(populated, start=start, end=end, mode="raw")
    assert len(scoped) < len(everything)


def test_bbox_filter_scopes_the_dataset(populated):
    start, end = _window()
    frame, _ = build_dataset(
        populated, start=start, end=end, mode="raw",
        bbox=BoundingBox(0.0, 10.0, 0.0, 10.0),
    )
    assert frame.empty


def test_forecast_rows_are_excluded_by_default(populated):
    populated.upsert_observations(
        [MarineObservation(latitude=30.0, longitude=-40.0,
                           timestamp=BASE_MS + 200 * 3_600_000,
                           wave_height_m=3.0, is_forecast=True)],
        source="open_meteo",
    )
    start = dt.datetime.fromtimestamp(BASE_MS / 1000, tz=dt.timezone.utc)
    end = start + dt.timedelta(days=30)
    excluded, _ = build_dataset(populated, start=start, end=end, mode="raw")
    included, _ = build_dataset(populated, start=start, end=end, mode="raw",
                                include_forecast=True)
    assert len(included) == len(excluded) + 1


# -- serialisation ---------------------------------------------------------


def test_parquet_round_trip_preserves_utc(populated):
    start, end = _window()
    frame, _ = build_dataset(populated, start=start, end=end, mode="raw")
    payload = to_parquet_bytes(frame)
    restored = pq.read_table(io.BytesIO(payload)).to_pandas()

    assert len(restored) == len(frame)
    assert str(restored["time"].dt.tz) == "UTC"
    assert restored["time"].iloc[0] == frame["time"].iloc[0]


def test_parquet_preserves_numeric_types(populated):
    start, end = _window()
    frame, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                             interval="1d")
    restored = pq.read_table(io.BytesIO(to_parquet_bytes(frame))).to_pandas()
    assert pd.api.types.is_integer_dtype(restored["observation_count"])
    assert pd.api.types.is_float_dtype(restored["sst_celsius"])


def test_csv_writes_explicit_utc_text(populated):
    """CSV has no type system; the zone survives as text or not at all."""
    start, end = _window()
    frame, _ = build_dataset(populated, start=start, end=end, mode="raw")
    text = to_csv_bytes(frame).decode("utf-8")
    header, first = text.splitlines()[0], text.splitlines()[1]
    assert header.startswith("time")
    assert first.split(",")[0].endswith("Z")


def test_empty_result_serialises_without_raising(storage):
    start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    frame, meta = build_dataset(storage, start=start,
                                end=start + dt.timedelta(days=1), mode="raw")
    assert meta["rows"] == 0
    assert to_csv_bytes(frame)
    assert to_parquet_bytes(frame)


def test_empty_and_populated_exports_share_a_schema(populated):
    """A query that matches nothing must not change the column set.

    Otherwise a pipeline built against a busy period breaks the first time it
    hits a quiet one, which is the hardest kind of failure to reproduce.
    """
    start, end = _window()
    busy, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                            interval="1d")
    quiet_start = dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc)
    quiet, meta = build_dataset(populated, start=quiet_start,
                                end=quiet_start + dt.timedelta(days=5),
                                mode="aggregated", interval="1d")
    assert meta["matched_observations"] == 0
    assert set(busy.columns) == set(quiet.columns)
    assert to_parquet_bytes(quiet)


def test_bundle_carries_everything_needed_to_interpret_it(populated):
    """A CSV of numbers is not a dataset.

    Units, provenance and caveats have to travel with the rows, or nobody -
    including the person who exported it - can reuse them six months later.
    """
    import zipfile

    from oceanpulse.exporting.manifest import build_bundle

    start, end = _window()
    frame, meta = build_dataset(populated, start=start, end=end, mode="aggregated",
                                interval="1d")
    spec = {"scope_mode": "global", "interval": "1d"}
    payload = build_bundle(frame, spec, meta, to_parquet_bytes(frame), "data.parquet")

    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        names = set(bundle.namelist())
        assert names == {"data.parquet", "manifest.json", "data_dictionary.csv", "README.txt"}

        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["result"]["rows"] == len(frame)
        assert manifest["query"] == spec
        assert manifest["providers"], "an export must name its data providers"
        assert manifest["caveats"], "an export must carry its caveats"
        # The direction conventions differ between waves and currents; that has
        # to be written down or the data will be misread.
        assert "FROM" in manifest["conventions"]["wave_direction"]
        assert "TOWARD" in manifest["conventions"]["current_direction"]

        readme = bundle.read("README.txt").decode()
        assert "OceanPulse data export" in readme
        assert "Caveats" in readme

        dictionary = bundle.read("data_dictionary.csv").decode()
        assert "nature" in dictionary.splitlines()[0]


def test_data_dictionary_covers_every_exported_column(populated):
    """A column with no dictionary entry is a column nobody can trust."""
    from oceanpulse.exporting.manifest import dictionary_for

    start, end = _window()
    for mode in ("raw", "aggregated"):
        frame, _ = build_dataset(populated, start=start, end=end, mode=mode,
                                 interval="1d", derived_features=True)
        table = dictionary_for(list(frame.columns))
        assert list(table["column"]) == list(frame.columns)
        assert table["nature"].isin(
            {"measurement", "analysis", "model", "derived", "metadata"}
        ).all()
        assert (table["description"].str.len() > 0).all()


def test_nature_distinguishes_measurement_from_model(populated):
    """The single most important field for honest reuse.

    Satellite altimetry, a wave model and something this software computed
    deserve very different amounts of trust.
    """
    from oceanpulse.exporting.manifest import dictionary_for

    table = dictionary_for(
        ["sea_level_anomaly_m", "wave_height_m", "wave_power_kw_m", "sst_celsius"]
    ).set_index("column")
    assert table.loc["sea_level_anomaly_m", "nature"] == "measurement"
    assert table.loc["wave_height_m", "nature"] == "model"
    assert table.loc["wave_power_kw_m", "nature"] == "derived"
    assert table.loc["sst_celsius", "nature"] == "analysis"


def test_derived_features_are_opt_in(populated):
    """Hourly gives enough points for a rolling statistic; daily does not."""
    start, end = _window()
    plain, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                             interval="1h")
    rich, meta = build_dataset(populated, start=start, end=end, mode="aggregated",
                               interval="1h", derived_features=True)
    assert meta["derived_features"] is True
    added = set(rich.columns) - set(plain.columns)
    assert any(name.endswith("_z") for name in added), added
    assert "log1p_wave_energy_kwh_m" in added
    # log1p keeps the genuine zeros that quiet windows contain.
    assert (rich["log1p_wave_energy_kwh_m"] >= 0).all()


def test_derived_features_refuse_a_series_too_short_to_standardise(populated):
    """A z-score from three points is noise wearing a statistic's clothes."""
    start, end = _window()
    short, _ = build_dataset(populated, start=start, end=end, mode="aggregated",
                             interval="1w", derived_features=True)
    assert not [c for c in short.columns if c.endswith("_z")]


def test_suggested_filenames_are_safe():
    name = suggest_filename("wpi:1234/../etc", "csv")
    assert "/" not in name and ".." not in name
    assert name.endswith(".csv")
