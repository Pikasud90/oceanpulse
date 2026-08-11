"""Data dictionary, manifest and bundle writer.

A CSV of numbers is not a dataset. Six months later, nobody — including the
person who exported it — can say what units a column is in, whether a value was
measured or modelled, which provider it came from, what licence it carries, or
what the known failure modes are. That information has to travel *with* the
data, not live in someone's memory or a chat log.

So every export can be downloaded as a bundle containing:

    data.csv / data.parquet   the rows
    manifest.json             what was asked for, when, from where, under what licence
    data_dictionary.csv       one row per column: unit, nature, provider, meaning
    README.txt                the same thing in prose, for a human opening the zip

`nature` is the field that matters most for honest reuse. It distinguishes a
satellite analysis from wave-model output from something this software computed,
because those three deserve very different amounts of trust.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from dataclasses import asdict, dataclass
from typing import Any, Sequence

import pandas as pd

from .. import __version__


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    unit: str
    nature: str
    provider: str
    description: str


# nature values used below:
#   measurement  - an instrument produced this
#   analysis     - observations combined with a model for a past time
#   model        - numerical model output; nothing was measured
#   derived      - computed by OceanPulse from the columns above
#   metadata     - bookkeeping, not a physical quantity
COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    ColumnSpec("time", "UTC timestamp", "metadata", "OceanPulse",
               "Observation time. Stored internally as integer epoch milliseconds UTC."),
    ColumnSpec("window_start", "UTC timestamp", "metadata", "OceanPulse",
               "Start of the aggregation window, inclusive."),
    ColumnSpec("latitude", "degrees north", "metadata", "source grid",
               "Cell centre latitude as returned by the provider, not as requested."),
    ColumnSpec("longitude", "degrees east", "metadata", "source grid",
               "Cell centre longitude, normalised to -180..180."),
    ColumnSpec("port_id", "identifier", "metadata", "NGA WPI / GeoNames",
               "Tracked port this row belongs to; empty for macro-grid cells."),
    ColumnSpec("wave_height_m", "m", "model", "Open-Meteo Marine",
               "Significant wave height (Hm0): about the mean of the highest third "
               "of waves. Individual waves reach roughly 1.8x this."),
    ColumnSpec("wave_period_s", "s", "model", "Open-Meteo Marine",
               "Peak wave period: the period of the most energetic spectral band."),
    ColumnSpec("wave_direction_deg", "degrees", "model", "Open-Meteo Marine",
               "Direction waves travel FROM, meteorological convention."),
    ColumnSpec("current_velocity_kmh", "km/h", "model", "Open-Meteo Marine",
               "Near-surface current speed, including wind-driven flow."),
    ColumnSpec("current_direction_deg", "degrees", "model", "Open-Meteo Marine",
               "Direction the current flows TOWARD, oceanographic convention. "
               "Note this is the opposite convention to wave direction."),
    ColumnSpec("sst_celsius", "degC", "analysis", "Open-Meteo Marine / NOAA OISST v2.1",
               "Sea-surface temperature. Hourly values are wave-model output; older "
               "values are the NOAA OISST satellite-plus-in-situ daily analysis. "
               "Hourly takes precedence where both exist."),
    ColumnSpec("sea_level_anomaly_m", "m", "measurement", "NOAA CoastWatch altimetry",
               "Sea-surface height minus the long-term mean surface at that location. "
               "Not a height above any land datum. Product lags real time by months."),
    ColumnSpec("geostrophic_u_ms", "m/s", "measurement", "NOAA CoastWatch altimetry",
               "Eastward geostrophic velocity from the sea-surface slope. "
               "Not meaningful within a few degrees of the equator."),
    ColumnSpec("geostrophic_v_ms", "m/s", "measurement", "NOAA CoastWatch altimetry",
               "Northward geostrophic velocity from the sea-surface slope."),
    ColumnSpec("wave_power_kw_m", "kW/m", "derived", "OceanPulse",
               "Deep-water wave energy flux, 0.49 * Hm0^2 * T. Uses peak period "
               "in place of energy period, which overstates power by about 11%."),
    ColumnSpec("wave_height_m_max", "m", "derived", "OceanPulse",
               "Maximum significant wave height within the aggregation window."),
    ColumnSpec("wave_power_kw_m_max", "kW/m", "derived", "OceanPulse",
               "Maximum wave power within the aggregation window."),
    ColumnSpec("wave_energy_kwh_m", "kWh/m", "derived", "OceanPulse",
               "Energy delivered over the window: mean power times window hours. "
               "Extensive, so quiet windows are genuinely zero."),
    ColumnSpec("observation_count", "count", "derived", "OceanPulse",
               "Number of observations aggregated into the window. Extensive: zero "
               "is the true value for a window with no data."),
    ColumnSpec("cumulative_observations", "count", "derived", "OceanPulse",
               "Running total of observation_count."),
    ColumnSpec("cumulative_wave_energy_kwh_m", "kWh/m", "derived", "OceanPulse",
               "Running total of wave_energy_kwh_m."),
    ColumnSpec("is_forecast", "boolean", "metadata", "OceanPulse",
               "1 when the row describes a time after the last observation, i.e. a "
               "model forecast. Excluded from exports unless explicitly included."),
    ColumnSpec("sources", "list", "metadata", "OceanPulse",
               "Comma-separated provenance: which ingest sources contributed to "
               "this row."),
    ColumnSpec("distance_km", "km", "derived", "OceanPulse",
               "Great-circle distance from the query centre, when a radius filter "
               "was used."),
)

_SPEC_BY_NAME = {spec.name: spec for spec in COLUMN_SPECS}

ATTRIBUTION = {
    "Open-Meteo Marine": {
        "role": "waves, currents, near-term sea-surface temperature",
        "nature": "numerical wave-model output, not measurements",
        "licence": "CC BY 4.0",
        "url": "https://open-meteo.com/",
        "coverage": "waves from 2021-12, currents from 2022-01, SST from 2022-12",
    },
    "NOAA OISST v2.1": {
        "role": "historical sea-surface temperature",
        "nature": "satellite plus in-situ analysis on a 0.25 degree daily grid",
        "licence": "public domain (U.S. Government work)",
        "url": "https://coastwatch.pfeg.noaa.gov/erddap/",
        "coverage": "1981-09 to about two weeks before present",
    },
    "NOAA CoastWatch altimetry": {
        "role": "sea level anomaly and geostrophic currents",
        "nature": "satellite radar altimetry, gridded",
        "licence": "public domain (U.S. Government work)",
        "url": "https://coastwatch.pfeg.noaa.gov/erddap/",
        "coverage": "2017-02 onwards, several months in arrears",
    },
    "NGA World Port Index": {
        "role": "port locations and harbour metadata",
        "nature": "reference gazetteer",
        "licence": "public domain (U.S. Government work)",
        "url": "https://msi.nga.mil/Publications/WPI",
        "coverage": "2,951 ports",
    },
    "GeoNames": {
        "role": "coastal place names",
        "nature": "reference gazetteer",
        "licence": "CC BY 4.0 (attribution required)",
        "url": "https://www.geonames.org/",
        "coverage": "coastal settlements, filtered against an ocean mask",
    },
}

CAVEATS = (
    "Most values here are model output or gridded analyses, not instrument "
    "readings. The `nature` column of the data dictionary says which is which.",
    "Variables begin at different dates. A model trained across one of those "
    "boundaries will learn a regime change that is an artefact of data "
    "availability, not of the ocean.",
    "The global grid is a sparse sample of a few hundred points and cannot "
    "resolve eddies, fronts or coastal processes. Use tracked ports for "
    "spatial questions.",
    "Sea level anomaly lags real time by months, so no sea-level value "
    "describes today.",
    "Wave power uses peak period in place of energy period and is therefore "
    "high by roughly 11%.",
    "Counts and summed energy are extensive: zero is the true value in a quiet "
    "window. Temperature, wave height and sea level are intensive: a zero "
    "there would be a false measurement, so gaps are left empty unless you "
    "chose otherwise.",
)


def dictionary_for(columns: Sequence[str]) -> pd.DataFrame:
    """Data dictionary limited to the columns actually present."""
    rows = []
    for name in columns:
        spec = _SPEC_BY_NAME.get(name)
        if spec is None:
            rows.append(
                {
                    "column": name,
                    "unit": "",
                    "nature": "derived",
                    "provider": "OceanPulse",
                    "description": "Derived column; see the Encyclopedia tab.",
                }
            )
        else:
            rows.append(
                {
                    "column": spec.name,
                    "unit": spec.unit,
                    "nature": spec.nature,
                    "provider": spec.provider,
                    "description": spec.description,
                }
            )
    return pd.DataFrame(rows)


def providers_for(columns: Sequence[str]) -> dict[str, Any]:
    """Only the attributions that this particular export actually needs."""
    needed: dict[str, Any] = {}
    for name in columns:
        spec = _SPEC_BY_NAME.get(name)
        if spec is None:
            continue
        for provider in spec.provider.split(" / "):
            provider = provider.strip()
            for key, value in ATTRIBUTION.items():
                if provider and (provider in key or key in provider):
                    needed[key] = value
    return needed


def build_manifest(
    frame: pd.DataFrame, spec: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    columns = list(frame.columns)
    time_col = "window_start" if "window_start" in columns else "time"
    covered: dict[str, Any] = {}
    if time_col in columns and not frame.empty:
        try:
            covered = {
                "first": str(frame[time_col].min()),
                "last": str(frame[time_col].max()),
            }
        except Exception:  # noqa: BLE001 - manifest must never break an export
            covered = {}

    return {
        "generated_by": f"OceanPulse {__version__}",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "query": spec,
        "result": {
            "rows": int(len(frame)),
            "columns": columns,
            "matched_observations": meta.get("matched_observations"),
            "truncated_at_row_cap": bool(meta.get("truncated")),
            "time_covered": covered,
        },
        "conventions": {
            "timestamps": "UTC. Parquet carries timezone-aware microseconds; CSV "
                          "uses ISO-8601 with a trailing Z.",
            "wave_direction": "direction waves travel FROM",
            "current_direction": "direction the current flows TOWARD",
            "longitude": "-180 to 180",
        },
        "providers": providers_for(columns),
        "caveats": list(CAVEATS),
        "data_dictionary": dictionary_for(columns).to_dict(orient="records"),
    }


def readme_text(manifest: dict[str, Any]) -> str:
    """The manifest as prose, for whoever opens the zip without a JSON viewer."""
    lines: list[str] = []
    lines.append("OceanPulse data export")
    lines.append("=" * 46)
    lines.append("")
    lines.append(f"Generated by : {manifest['generated_by']}")
    lines.append(f"Generated at : {manifest['generated_at_utc']}")
    result = manifest.get("result", {})
    lines.append(f"Rows         : {result.get('rows', 0):,}")
    covered = result.get("time_covered") or {}
    if covered:
        lines.append(f"Period       : {covered.get('first')}  ..  {covered.get('last')}")
    if result.get("truncated_at_row_cap"):
        lines.append("NOTE         : truncated at the configured row cap.")
    lines.append("")

    lines.append("Query")
    lines.append("-" * 46)
    for key, value in (manifest.get("query") or {}).items():
        lines.append(f"  {key:18} {value}")
    lines.append("")

    lines.append("Conventions")
    lines.append("-" * 46)
    for key, value in (manifest.get("conventions") or {}).items():
        lines.append(f"  {key:18} {value}")
    lines.append("")

    lines.append("Columns")
    lines.append("-" * 46)
    for entry in manifest.get("data_dictionary", []):
        unit = f" [{entry['unit']}]" if entry["unit"] else ""
        lines.append(f"  {entry['column']}{unit}")
        lines.append(f"      nature   : {entry['nature']}")
        lines.append(f"      provider : {entry['provider']}")
        lines.append(f"      meaning  : {entry['description']}")
    lines.append("")

    lines.append("Sources and licences")
    lines.append("-" * 46)
    for name, info in (manifest.get("providers") or {}).items():
        lines.append(f"  {name}")
        for key in ("role", "nature", "coverage", "licence", "url"):
            if info.get(key):
                lines.append(f"      {key:9}: {info[key]}")
    lines.append("")

    lines.append("Caveats you must carry into any analysis")
    lines.append("-" * 46)
    for index, caveat in enumerate(manifest.get("caveats", []), start=1):
        lines.append(f"  {index}. {caveat}")
    lines.append("")
    lines.append(
        "This data is for research and education. It is not a navigational aid "
        "and not an emergency warning system."
    )
    return "\n".join(lines) + "\n"


def build_bundle(
    frame: pd.DataFrame,
    spec: dict[str, Any],
    meta: dict[str, Any],
    data_bytes: bytes,
    data_filename: str,
) -> bytes:
    """Zip the rows together with everything needed to interpret them."""
    manifest = build_manifest(frame, spec, meta)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(data_filename, data_bytes)
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        bundle.writestr(
            "data_dictionary.csv",
            dictionary_for(list(frame.columns)).to_csv(index=False),
        )
        bundle.writestr("README.txt", readme_text(manifest))
    return buffer.getvalue()
