"""Tab 1 — Global Marine Pulse.

Everything on this tab reads **only** from local storage. It never contacts an
upstream service, so it stays responsive with the network unplugged and its
refresh rate is unrelated to the polling interval.

Two presentation problems drove the design here.

**Arrows drown in dots.** Drawing a few hundred sample points and a few hundred
current arrows in one colour produces noise, not a flow field. Currents are now
their own layer, split into labelled speed bands with their own colours and
legend entries, with adjustable density and the option to hide the points
entirely. A field you can actually read beats a field with more data in it.

**A dot with no name is not information.** Points are clickable and the severe
sea-state list expands, both resolving to the nearest named port with a bearing
and distance, so a reader can tell *where* something is without decoding
signed decimals.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd
from dash import Input, Output, State, dcc, html, no_update

from ..logging_setup import get_logger
from ..math_engine import haversine_km, rolling_zscore, wave_energy_flux_series
from ..storage.base import ObservationFilter
from . import theme
from .services import Services

log = get_logger(__name__)

SEVERE_WAVE_HEIGHT_M = 5.0
REFRESH_MS = 60_000

COLOUR_OPTIONS = {
    "sst_celsius": ("Sea surface temperature", "°C", theme.SST_SCALE),
    "wave_power_kw_m": ("Wave power", "kW/m", theme.POWER_SCALE),
    "wave_height_m": ("Significant wave height", "m", theme.WAVE_SCALE),
    "current_velocity_kmh": ("Current speed", "km/h", theme.CURRENT_SCALE),
}

# Speed bands for the current layer. Each becomes its own trace so it can carry
# a colour and a legend entry - a single multi-segment line cannot.
CURRENT_BANDS = (
    (0.0, 2.0, "0–2 km/h", "#4a7fa5"),
    (2.0, 5.0, "2–5 km/h", "#3fb8c4"),
    (5.0, 10.0, "5–10 km/h", "#f5d24a"),
    (10.0, 1e9, "over 10 km/h", "#ff7a45"),
)

ARROW_DENSITY = {"off": 0.0, "sparse": 0.25, "normal": 0.6, "all": 1.0}


def layout(services: Services) -> html.Div:
    return html.Div(
        [
            dcc.Interval(id="pulse-refresh", interval=REFRESH_MS, n_intervals=0),
            dcc.Store(id="pulse-selected-cell"),
            html.Div(id="pulse-kpis", className="op-grid op-kpis"),
            html.Div(className="op-spacer"),
            html.Div(
                [
                    # ---------------- map column ----------------
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label("Projection"),
                                            dcc.RadioItems(
                                                id="pulse-projection",
                                                options=[
                                                    {"label": " Flat map", "value": "natural earth"},
                                                    {"label": " Globe", "value": "orthographic"},
                                                ],
                                                value="natural earth",
                                                inline=True,
                                                inputStyle={"marginRight": "5px"},
                                                labelStyle={"marginRight": "14px"},
                                            ),
                                        ],
                                        className="op-field",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Colour points by"),
                                            dcc.Dropdown(
                                                id="pulse-colour",
                                                options=[
                                                    {
                                                        "label": f"{label} ({unit})",
                                                        "value": key,
                                                    }
                                                    for key, (label, unit, _) in
                                                    COLOUR_OPTIONS.items()
                                                ],
                                                value="sst_celsius",
                                                clearable=False,
                                                style={"width": "270px"},
                                            ),
                                        ],
                                        className="op-field",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Current arrows"),
                                            dcc.Dropdown(
                                                id="pulse-arrows",
                                                options=[
                                                    {"label": "Off", "value": "off"},
                                                    {"label": "Sparse — strongest only", "value": "sparse"},
                                                    {"label": "Normal", "value": "normal"},
                                                    {"label": "All cells", "value": "all"},
                                                ],
                                                value="normal",
                                                clearable=False,
                                                style={"width": "215px"},
                                            ),
                                        ],
                                        className="op-field",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Sample points"),
                                            dcc.Checklist(
                                                id="pulse-show-points",
                                                options=[{"label": " Show", "value": "on"}],
                                                value=["on"],
                                                inputStyle={"marginRight": "5px"},
                                            ),
                                        ],
                                        className="op-field",
                                    ),
                                ],
                                className="op-row",
                                style={"marginBottom": "12px"},
                            ),
                            dcc.Graph(
                                id="pulse-map",
                                config={"displayModeBar": False, "scrollZoom": True},
                                style={"height": "560px"},
                            ),
                            html.Div(
                                "Click any point for its full readings, a compass "
                                "and its nearest port. Arrows point the way the "
                                "water is going; length and colour both scale with "
                                "speed. Points are a sparse sample — the gaps "
                                "between them are not measurements.",
                                className="op-kpi-sub",
                                style={"marginTop": "8px"},
                            ),
                        ],
                        className="op-card",
                        style={"flex": "3 1 660px", "minWidth": "0"},
                    ),
                    # ---------------- side column ----------------
                    html.Div(
                        [
                            html.Div(id="pulse-detail", className="op-card"),
                            html.Div(className="op-spacer"),
                            html.Div(
                                [
                                    html.Div(
                                        "Severe sea state",
                                        className="op-card-title",
                                    ),
                                    html.Div(
                                        f"Cells reporting significant wave height "
                                        f"at or above {SEVERE_WAVE_HEIGHT_M:.1f} m. "
                                        f"Click a row for full detail.",
                                        className="op-card-sub",
                                    ),
                                    html.Div(id="pulse-ticker", className="op-ticker"),
                                ],
                                className="op-card",
                            ),
                        ],
                        style={"flex": "1 1 330px", "minWidth": "0"},
                    ),
                ],
                className="op-split",
            ),
            html.Div(className="op-spacer"),
            html.Div(
                [
                    dcc.Graph(
                        id="pulse-distribution",
                        config={"displayModeBar": False},
                        style={"height": "330px"},
                    )
                ],
                className="op-card",
            ),
            html.Div(className="op-spacer"),
            html.Div(
                [
                    html.B("What you are looking at. "),
                    "Wave, current and near-term temperature values are output "
                    "from a numerical wave model, not buoy measurements. Rows "
                    "with future timestamps are forecasts and are excluded here. "
                    "See the Encyclopedia tab for what each quantity means and "
                    "how far it can be trusted.",
                ],
                className="op-note",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def latest_grid_frame(services: Services, hours: int = 12) -> pd.DataFrame:
    """Most recent analysis value for each sampled cell."""
    now = dt.datetime.now(dt.timezone.utc)
    rows = services.storage.query_observations(
        ObservationFilter(
            start_ms=int((now - dt.timedelta(hours=hours)).timestamp() * 1000),
            end_ms=int(now.timestamp() * 1000),
            include_forecast=False,
            order="asc",
        )
    )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["time"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame["wave_power_kw_m"] = wave_energy_flux_series(
        frame.get("wave_height_m", pd.Series(dtype=float)),
        frame.get("wave_period_s", pd.Series(dtype=float)),
    )
    frame["cell"] = (
        frame["latitude"].round(3).astype(str) + "," + frame["longitude"].round(3).astype(str)
    )
    return (
        frame.sort_values("timestamp")
        .groupby("cell", as_index=False)
        .last()
        .reset_index(drop=True)
    )


def _nearest_place(services: Services, lat: float, lon: float) -> dict[str, Any] | None:
    try:
        found = services.gazetteer.nearest(lat, lon, limit=1)
    except Exception:  # noqa: BLE001 - naming is a nicety, never a failure
        return None
    return found[0] if found else None


def _kpi(label: str, value: str, sub: str = "") -> html.Div:
    return html.Div(
        [
            html.Div(label, className="op-kpi-label"),
            html.Div(value, className="op-kpi-value"),
            html.Div(sub, className="op-kpi-sub"),
        ],
        className="op-card",
    )


def _sst_anomaly_summary(services: Services) -> tuple[float | None, int]:
    """Highest current SST anomaly, and how many cells look like a warm spell.

    A rolling-baseline z-score against each cell's own recent history, not a
    30-year climatology. Labelled as an approximation wherever it is shown.
    """
    now = dt.datetime.now(dt.timezone.utc)
    rows = services.storage.query_observations(
        ObservationFilter(
            start_ms=int((now - dt.timedelta(days=45)).timestamp() * 1000),
            end_ms=int(now.timestamp() * 1000),
            include_forecast=False,
            require_columns=("sst_celsius",),
            order="asc",
        )
    )
    if not rows:
        return None, 0
    frame = pd.DataFrame(rows)
    frame["cell"] = (
        frame["latitude"].round(3).astype(str) + "," + frame["longitude"].round(3).astype(str)
    )
    peak: float | None = None
    warm_cells = 0
    for _, group in frame.groupby("cell"):
        if len(group) < 24:
            continue
        daily = (
            group.assign(
                day=pd.to_datetime(group["timestamp"], unit="ms", utc=True).dt.floor("1D")
            )
            .groupby("day")["sst_celsius"]
            .mean()
        )
        if len(daily) < 10:
            continue
        z = rolling_zscore(daily.tolist(), window=30, min_periods=8)
        finite = z[np.isfinite(z)]
        if finite.size == 0:
            continue
        latest = float(finite[-1])
        if peak is None or latest > peak:
            peak = latest
        if latest > 2.0:
            warm_cells += 1
    return peak, warm_cells


# ---------------------------------------------------------------------------
# Compass
# ---------------------------------------------------------------------------


def _compass_figure(wave_dir: float | None, current_dir: float | None) -> dict[str, Any]:
    """A real compass: north up, clockwise, with a needle per direction.

    Built as a polar plot rather than CSS so the angular axis, the cardinal
    labels and the tick marks come out correct by construction.
    """
    data: list[dict[str, Any]] = []
    for bearing, colour, label in (
        (current_dir, theme.ACCENT, "Current"),
        (wave_dir, theme.WARNING, "Waves"),
    ):
        if bearing is None:
            continue
        data.append(
            {
                "type": "scatterpolar",
                "r": [0, 1],
                "theta": [float(bearing), float(bearing)],
                "mode": "lines+markers",
                "line": {"color": colour, "width": 3},
                "marker": {"size": [0, 11], "symbol": "triangle-up", "color": colour,
                           "angleref": "previous"},
                "name": f"{label} {theme.bearing_text(bearing)}",
                "hovertemplate": f"{label} {theme.bearing_text(bearing)}<extra></extra>",
            }
        )

    if not data:
        return theme.empty_figure("No direction reported here")

    layout = theme.figure_layout(
        margin={"l": 26, "r": 26, "t": 10, "b": 34},
        showlegend=True,
        legend={"y": -0.08, "x": 0.5, "xanchor": "center", "font": {"size": 10},
                "bgcolor": "rgba(0,0,0,0)", "bordercolor": "rgba(0,0,0,0)"},
        polar={
            "bgcolor": "rgba(255,255,255,0.03)",
            "radialaxis": {"visible": False, "range": [0, 1.05]},
            "angularaxis": {
                "direction": "clockwise",
                "rotation": 90,
                "tickmode": "array",
                "tickvals": [0, 45, 90, 135, 180, 225, 270, 315],
                "ticktext": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
                "gridcolor": "rgba(255,255,255,0.12)",
                "linecolor": "rgba(255,255,255,0.18)",
                "tickfont": {"size": 10, "color": theme.TEXT_MUTED},
            },
        },
    )
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    return {"data": data, "layout": layout}


# ---------------------------------------------------------------------------
# Detail panel
# ---------------------------------------------------------------------------


def _detail_rows(row: pd.Series) -> list[Any]:
    band, band_colour = theme.sea_state(row.get("wave_height_m"))
    pairs = [
        ("Position", theme.format_latlon(row.get("latitude"), row.get("longitude"))),
        ("Sea state", band),
        ("Wave height", theme.format_number(row.get("wave_height_m"), 2, " m")),
        ("Wave period", theme.format_number(row.get("wave_period_s"), 1, " s")),
        ("Wave from", theme.bearing_text(row.get("wave_direction_deg"))),
        ("Wave power", theme.format_number(row.get("wave_power_kw_m"), 1, " kW/m")),
        ("Current speed", theme.format_number(row.get("current_velocity_kmh"), 2, " km/h")),
        ("Current toward", theme.bearing_text(row.get("current_direction_deg"))),
        ("Sea temperature", theme.format_number(row.get("sst_celsius"), 2, " °C")),
        ("Sea level anomaly",
         theme.format_number(
             (row.get("sea_level_anomaly_m") * 100)
             if row.get("sea_level_anomaly_m") is not None
             and pd.notna(row.get("sea_level_anomaly_m"))
             else None,
             1, " cm",
         )),
        ("Observed", theme.format_utc(row.get("timestamp")) + " UTC"),
        ("Age", theme.format_relative(row.get("timestamp"))),
    ]
    return [
        html.Div(
            [
                html.Div(key, className="op-detail-key"),
                html.Div(
                    value,
                    className="op-detail-val",
                    style={"color": band_colour} if key == "Sea state" else None,
                ),
            ]
        )
        for key, value in pairs
    ]


def _detail_panel(services: Services, row: pd.Series | None) -> list[Any]:
    if row is None:
        return [
            html.Div("Point detail", className="op-card-title"),
            html.Div(
                "Click a point on the map to see every reading for that cell, "
                "a compass for wave and current direction, and the nearest port "
                "with its bearing and distance.",
                className="op-card-sub",
            ),
        ]

    lat = float(row["latitude"])
    lon = float(row["longitude"])
    place = _nearest_place(services, lat, lon)

    header: list[Any] = [
        html.Div(
            [
                "Selected cell",
                html.Span(
                    theme.sea_state(row.get("wave_height_m"))[0],
                    className="op-badge",
                    style={"color": theme.sea_state(row.get("wave_height_m"))[1],
                           "marginLeft": "auto"},
                ),
            ],
            className="op-card-title",
        )
    ]
    if place:
        bearing = theme.initial_bearing(lat, lon, place["latitude"], place["longitude"])
        distance = haversine_km(lat, lon, place["latitude"], place["longitude"])
        header.append(
            html.Div(
                [
                    html.B(place["port_name"]),
                    html.Span(
                        f" ({place.get('country_name') or place.get('country_code') or '—'})"
                    ),
                    html.Span(
                        f" · {distance:,.0f} km away, bearing "
                        f"{theme.bearing_text(bearing)} from here",
                        style={"color": theme.TEXT_MUTED},
                    ),
                ],
                className="op-card-sub",
            )
        )
    else:
        header.append(
            html.Div(
                theme.format_latlon(lat, lon) + " · open ocean, no port nearby",
                className="op-card-sub",
            )
        )

    return [
        *header,
        html.Div(
            [
                dcc.Graph(
                    figure=_compass_figure(
                        row.get("wave_direction_deg"), row.get("current_direction_deg")
                    ),
                    config={"displayModeBar": False},
                    style={"height": "190px", "flex": "1 1 190px", "minWidth": "180px"},
                ),
            ],
            className="op-compass-wrap",
        ),
        html.Div(_detail_rows(row), className="op-detail-grid op-detail"),
    ]


# ---------------------------------------------------------------------------
# Severe sea state list
# ---------------------------------------------------------------------------


def _severe_rows(services: Services, frame: pd.DataFrame) -> Any:
    severe = frame[frame["wave_height_m"] >= SEVERE_WAVE_HEIGHT_M].sort_values(
        "wave_height_m", ascending=False
    )
    if severe.empty:
        return html.Div(
            f"No sampled cell is currently at or above {SEVERE_WAVE_HEIGHT_M:.1f} m. "
            f"The highest right now is "
            f"{theme.format_number(frame['wave_height_m'].max(), 1, ' m')}.",
            className="op-kpi-sub",
        )

    rows: list[Any] = []
    for _, row in severe.head(40).iterrows():
        lat, lon = float(row["latitude"]), float(row["longitude"])
        place = _nearest_place(services, lat, lon)
        band, band_colour = theme.sea_state(row.get("wave_height_m"))

        if place:
            distance = haversine_km(lat, lon, place["latitude"], place["longitude"])
            where = f"{distance:,.0f} km from {place['port_name']}"
        else:
            where = "Open ocean"

        rows.append(
            # html.Details gives expand/collapse natively, with no callback and
            # no state to keep in sync.
            html.Details(
                [
                    html.Summary(
                        html.Div(
                            [
                                html.Div(
                                    theme.format_number(row["wave_height_m"], 1, " m"),
                                    className="op-listrow-metric",
                                    style={"color": band_colour},
                                ),
                                html.Div(
                                    [
                                        html.Div(where, className="op-listrow-place"),
                                        html.Div(
                                            theme.format_latlon(lat, lon),
                                            className="op-listrow-coords",
                                        ),
                                    ],
                                    className="op-listrow-where",
                                ),
                                html.Span(
                                    band,
                                    className="op-badge",
                                    style={"color": band_colour},
                                ),
                            ],
                            className="op-listrow-head",
                        ),
                        style={"cursor": "pointer", "listStyle": "none"},
                    ),
                    html.Div(_detail_rows(row), className="op-detail-grid op-detail"),
                ],
                className="op-listrow",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Map traces
# ---------------------------------------------------------------------------


def _current_traces(frame: pd.DataFrame, mode: str) -> list[dict[str, Any]]:
    """Ocean current arrows, banded by speed.

    Plotly has no vector-field trace for geographic axes, so each arrow is a
    short two-point line with a None separator. One trace per speed band means
    each band gets a colour and a legend entry, which is the difference between
    a readable flow field and a grey haystack.
    """
    fraction = ARROW_DENSITY.get(mode, 0.6)
    if fraction <= 0:
        return []

    subset = frame.dropna(subset=["current_velocity_kmh", "current_direction_deg"])
    if subset.empty:
        return []

    if fraction < 1.0:
        # Keep the strongest flows: those are the ones carrying the structure.
        keep = max(1, int(len(subset) * fraction))
        subset = subset.nlargest(keep, "current_velocity_kmh")

    peak = max(1e-6, float(subset["current_velocity_kmh"].max()))
    traces: list[dict[str, Any]] = []

    for low, high, label, colour in CURRENT_BANDS:
        band = subset[
            (subset["current_velocity_kmh"] >= low) & (subset["current_velocity_kmh"] < high)
        ]
        if band.empty:
            continue
        lats: list[float | None] = []
        lons: list[float | None] = []
        for _, row in band.iterrows():
            speed = float(row["current_velocity_kmh"])
            # Oceanographic convention: direction is where the flow is going.
            bearing = np.radians(float(row["current_direction_deg"]))
            length = 1.6 + 6.0 * (speed / peak)
            lat0, lon0 = float(row["latitude"]), float(row["longitude"])
            lat1 = lat0 + length * np.cos(bearing)
            lon1 = lon0 + length * np.sin(bearing) / max(0.25, np.cos(np.radians(lat0)))
            lats.extend([lat0, lat1, None])
            lons.extend([lon0, lon1, None])

        traces.append(
            {
                "type": "scattergeo",
                "lat": lats,
                "lon": lons,
                "mode": "lines",
                "line": {"width": 1.6, "color": colour},
                "opacity": 0.9,
                "hoverinfo": "skip",
                "name": label,
                "legendgroup": "currents",
                "legendgrouptitle": {"text": "Current speed"},
            }
        )
    return traces


def _map_figure(
    frame: pd.DataFrame,
    projection: str,
    colour_by: str,
    arrows: str,
    show_points: bool,
    selected: pd.Series | None,
) -> dict[str, Any]:
    if frame.empty:
        return theme.empty_figure(
            "No observations yet",
            "The first ingestion cycle populates this map, usually within a minute.",
        )

    label, unit, scale = COLOUR_OPTIONS.get(
        colour_by, ("Value", "", theme.WAVE_SCALE)
    )
    current_traces = _current_traces(frame, arrows)
    data: list[dict[str, Any]] = list(current_traces)

    if show_points:
        points = frame.dropna(subset=[colour_by])
        if points.empty:
            data.append(
                {
                    "type": "scattergeo",
                    "lat": [],
                    "lon": [],
                    "mode": "markers",
                    "name": f"no {label.lower()} reported",
                }
            )
        else:
            bands = [theme.sea_state(v)[0] for v in points["wave_height_m"]]
            data.append(
                {
                    "type": "scattergeo",
                    "lat": points["latitude"],
                    "lon": points["longitude"],
                    "mode": "markers",
                    "name": label,
                    "showlegend": False,
                    "marker": {
                        "size": 10,
                        "color": points[colour_by],
                        "colorscale": scale,
                        "showscale": True,
                        "colorbar": {
                            "title": {"text": f"{label}<br>({unit})", "side": "right",
                                      "font": {"size": 11}},
                            "thickness": 13,
                            "len": 0.72,
                            "outlinewidth": 0,
                            "tickfont": {"size": 10},
                        },
                        "line": {"width": 0.6, "color": "rgba(255,255,255,0.4)"},
                    },
                    "customdata": np.stack(
                        [
                            points["cell"],
                            points["wave_height_m"].fillna(-1),
                            points["wave_period_s"].fillna(-1),
                            points["current_velocity_kmh"].fillna(-1),
                            points["sst_celsius"].fillna(-999),
                            bands,
                        ],
                        axis=-1,
                    ),
                    "hovertemplate": (
                        "<b>%{lat:.2f}, %{lon:.2f}</b><br>"
                        "Sea state: %{customdata[5]}<br>"
                        "Wave height: %{customdata[1]:.2f} m<br>"
                        "Wave period: %{customdata[2]:.1f} s<br>"
                        "Current: %{customdata[3]:.1f} km/h<br>"
                        "Sea temp: %{customdata[4]:.2f} °C"
                        "<extra>click for full detail</extra>"
                    ),
                }
            )

    if selected is not None:
        data.append(
            {
                "type": "scattergeo",
                "lat": [float(selected["latitude"])],
                "lon": [float(selected["longitude"])],
                "mode": "markers",
                "marker": {
                    "size": 20,
                    "color": "rgba(0,0,0,0)",
                    "line": {"width": 2.5, "color": theme.ACCENT},
                },
                "name": "selected",
                "hoverinfo": "skip",
                "showlegend": False,
            }
        )

    layout = theme.figure_layout(
        margin={"l": 0, "r": 0, "t": 6, "b": 0},
        geo={
            "projection": {"type": projection},
            "showland": True,
            "landcolor": "#1b324e",
            "showocean": True,
            "oceancolor": "#04121e",
            "showcoastlines": True,
            "coastlinecolor": "#3d7ba6",
            "coastlinewidth": 0.8,
            "showframe": False,
            "bgcolor": "rgba(0,0,0,0)",
            "lataxis": {"showgrid": True, "gridcolor": "rgba(255,255,255,0.05)"},
            "lonaxis": {"showgrid": True, "gridcolor": "rgba(255,255,255,0.05)"},
        },
        showlegend=bool(current_traces),
        legend={
            "y": 0.02,
            "x": 0.01,
            "yanchor": "bottom",
            "orientation": "v",
            "font": {"size": 10},
        },
    )
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    return {"data": data, "layout": layout}


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def register(app: Any, services: Services) -> None:
    @app.callback(
        Output("pulse-kpis", "children"),
        Output("pulse-ticker", "children"),
        Input("pulse-refresh", "n_intervals"),
    )
    def _refresh(_ticks: int):
        frame = latest_grid_frame(services)
        if frame.empty:
            return (
                [
                    _kpi("Peak wave power", "—", "waiting for first ingestion cycle"),
                    _kpi("Highest SST anomaly", "—", "needs several days of history"),
                    _kpi("Warm-spell cells", "—", "rolling-baseline approximation"),
                    _kpi("Sampled cells", "0", "grid not yet populated"),
                ],
                html.Div("No data yet.", className="op-kpi-sub"),
            )

        power = frame["wave_power_kw_m"].dropna()
        peak_power = float(power.max()) if not power.empty else None
        peak_row = frame.loc[frame["wave_power_kw_m"].idxmax()] if not power.empty else None
        anomaly, warm_cells = _sst_anomaly_summary(services)

        peak_where = ""
        if peak_row is not None:
            place = _nearest_place(
                services, float(peak_row["latitude"]), float(peak_row["longitude"])
            )
            peak_where = theme.format_latlon(peak_row["latitude"], peak_row["longitude"])
            if place:
                peak_where += f" · nearest {place['port_name']}"

        kpis = [
            _kpi(
                "Peak wave power",
                theme.format_number(peak_power, 1, " kW/m"),
                peak_where or "energy flux per metre of crest",
            ),
            _kpi(
                "Highest SST anomaly",
                theme.format_number(anomaly, 2, " σ") if anomaly is not None else "—",
                "vs each cell's own recent baseline",
            ),
            _kpi(
                "Warm-spell cells",
                theme.format_count(warm_cells),
                "above +2σ — approximation, not a heatwave count",
            ),
            _kpi(
                "Sampled cells",
                theme.format_count(len(frame)),
                f"newest reading {theme.format_relative(int(frame['timestamp'].max()))}",
            ),
        ]
        return kpis, _severe_rows(services, frame)

    @app.callback(
        Output("pulse-selected-cell", "data"),
        Input("pulse-map", "clickData"),
        prevent_initial_call=True,
    )
    def _select(click: dict[str, Any] | None):
        if not click:
            return no_update
        for point in click.get("points", []):
            custom = point.get("customdata")
            # Arrow traces carry no customdata; only the points layer is
            # selectable, so a click on the flow field is simply ignored.
            if isinstance(custom, (list, tuple)) and custom:
                return {"cell": str(custom[0])}
        return no_update

    @app.callback(
        Output("pulse-map", "figure"),
        Output("pulse-detail", "children"),
        Input("pulse-refresh", "n_intervals"),
        Input("pulse-projection", "value"),
        Input("pulse-colour", "value"),
        Input("pulse-arrows", "value"),
        Input("pulse-show-points", "value"),
        Input("pulse-selected-cell", "data"),
    )
    def _map(_ticks, projection, colour_by, arrows, show_points, selected_data):
        frame = latest_grid_frame(services)
        selected_row: pd.Series | None = None
        if selected_data and not frame.empty:
            match = frame[frame["cell"] == selected_data.get("cell")]
            if not match.empty:
                selected_row = match.iloc[0]

        figure = _map_figure(
            frame,
            projection or "natural earth",
            colour_by or "sst_celsius",
            arrows or "normal",
            bool(show_points and "on" in show_points),
            selected_row,
        )
        return figure, _detail_panel(services, selected_row)

    @app.callback(
        Output("pulse-distribution", "figure"),
        Input("pulse-refresh", "n_intervals"),
    )
    def _distribution(_ticks: int):
        frame = latest_grid_frame(services)
        if frame.empty:
            return theme.empty_figure("No observations yet")
        power = frame["wave_power_kw_m"].dropna()
        if power.empty:
            return theme.empty_figure(
                "Wave power needs both height and period",
                "No sampled cell is currently reporting both.",
            )

        median = float(power.median())
        p99 = float(power.quantile(0.99))
        counts, edges = np.histogram(power, bins=44)
        centres = (edges[:-1] + edges[1:]) / 2.0

        return {
            "data": [
                {
                    "type": "bar",
                    "x": centres,
                    "y": counts,
                    "width": (edges[1] - edges[0]) * 0.92,
                    "marker": {"color": theme.ACCENT, "line": {"width": 0}},
                    "name": "Sampled cells",
                    "hovertemplate": "%{y} cells near %{x:.0f} kW/m<extra></extra>",
                },
                # Reference lines carried as legend entries so they are named
                # rather than being unexplained marks on the axis.
                {
                    "type": "scatter",
                    "x": [median, median],
                    "y": [0, counts.max() * 1.04],
                    "mode": "lines",
                    "line": {"color": theme.WARNING, "width": 2, "dash": "dash"},
                    "name": f"Median {median:.1f} kW/m",
                    "hoverinfo": "skip",
                },
                {
                    "type": "scatter",
                    "x": [p99, p99],
                    "y": [0, counts.max() * 1.04],
                    "mode": "lines",
                    "line": {"color": theme.DANGER, "width": 2, "dash": "dot"},
                    "name": f"99th percentile {p99:.0f} kW/m",
                    "hoverinfo": "skip",
                },
            ],
            "layout": theme.figure_layout(
                title=theme.chart_title(
                    "Wave power distribution across sampled cells",
                    "Energy flux per metre of wave crest. Strongly right-skewed: "
                    "most of the ocean is calm while a small fraction carries "
                    "most of the power.",
                ),
                xaxis={"title": {"text": "Wave power (kW/m)"}},
                yaxis={"title": {"text": "Number of sampled cells"}},
                bargap=0.06,
                showlegend=True,
            ),
        }
