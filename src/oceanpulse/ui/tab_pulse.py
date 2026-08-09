"""Tab 1 — Global Marine Pulse.

Everything on this tab reads **only** from local storage. It never contacts an
upstream service, so it stays responsive with the network unplugged and its
refresh rate is unrelated to the polling interval.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd
from dash import Input, Output, dcc, html

from ..logging_setup import get_logger
from ..math_engine import rolling_zscore, wave_energy_flux_series
from ..storage.base import ObservationFilter
from . import theme
from .services import Services

log = get_logger(__name__)

SEVERE_WAVE_HEIGHT_M = 5.0
REFRESH_MS = 60_000


def layout(services: Services) -> html.Div:
    return html.Div(
        [
            dcc.Interval(id="pulse-refresh", interval=REFRESH_MS, n_intervals=0),
            html.Div(id="pulse-kpis", className="op-grid op-kpis"),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
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
                                            html.Label("Colour by"),
                                            dcc.Dropdown(
                                                id="pulse-colour",
                                                options=[
                                                    {"label": "Sea surface temperature", "value": "sst_celsius"},
                                                    {"label": "Wave power (kW/m)", "value": "wave_power_kw_m"},
                                                    {"label": "Wave height (m)", "value": "wave_height_m"},
                                                ],
                                                value="sst_celsius",
                                                clearable=False,
                                                style={"width": "260px", "color": "#0d2840"},
                                            ),
                                        ],
                                        className="op-field",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Overlay"),
                                            dcc.Checklist(
                                                id="pulse-vectors",
                                                options=[{"label": " Current vectors", "value": "on"}],
                                                value=["on"],
                                                inputStyle={"marginRight": "5px"},
                                            ),
                                        ],
                                        className="op-field",
                                    ),
                                ],
                                className="op-row",
                                style={"marginBottom": "10px"},
                            ),
                            dcc.Graph(
                                id="pulse-map",
                                config={"displayModeBar": False, "scrollZoom": True},
                                style={"height": "560px"},
                            ),
                        ],
                        className="op-card",
                        style={"flex": "3 1 720px"},
                    ),
                    html.Div(
                        [
                            html.Div("Severe sea state", style={"fontWeight": 650}),
                            html.Div(
                                f"Cells reporting significant wave height above "
                                f"{SEVERE_WAVE_HEIGHT_M:.1f} m",
                                className="op-kpi-sub",
                                style={"marginBottom": "10px"},
                            ),
                            html.Div(id="pulse-ticker", className="op-ticker"),
                        ],
                        className="op-card",
                        style={"flex": "1 1 300px"},
                    ),
                ],
                style={"display": "flex", "gap": "14px", "flexWrap": "wrap"},
            ),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    dcc.Graph(
                        id="pulse-distribution",
                        config={"displayModeBar": False},
                        style={"height": "300px"},
                    )
                ],
                className="op-card",
            ),
            html.Div(style={"height": "12px"}),
            html.Div(
                "Open-Meteo marine values are wave-model output, not buoy measurements. "
                "Cells are a sparse global sample, not a continuous field — the map shows "
                "where OceanPulse is looking, not everywhere the ocean is.",
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

    This is a rolling-baseline z-score against each cell's own recent history,
    not a 30-year climatology. It is labelled as an approximation everywhere
    it is shown.
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
            group.assign(day=pd.to_datetime(group["timestamp"], unit="ms", utc=True).dt.floor("1D"))
            .groupby("day")["sst_celsius"]
            .mean()
        )
        if len(daily) < 10:
            continue
        z = rolling_zscore(daily.tolist(), window=30, min_periods=8)
        finite = z[np.isfinite(z)]
        if finite.size == 0:
            continue
        latest = finite[-1]
        if peak is None or latest > peak:
            peak = float(latest)
        if latest > 2.0:
            warm_cells += 1
    return peak, warm_cells


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
                    _kpi("Warmest anomaly", "—", "needs several days of history"),
                    _kpi("Warm-spell cells", "—", "rolling-baseline approximation"),
                    _kpi("Sampled cells", "0", "grid not yet populated"),
                ],
                html.Div("No data yet.", className="op-kpi-sub"),
            )

        power = frame["wave_power_kw_m"].dropna()
        peak_power = float(power.max()) if not power.empty else None
        peak_row = (
            frame.loc[frame["wave_power_kw_m"].idxmax()]
            if not power.empty
            else None
        )
        anomaly, warm_cells = _sst_anomaly_summary(services)

        kpis = [
            _kpi(
                "Peak wave power",
                theme.format_number(peak_power, 1, " kW/m"),
                f"at {peak_row['latitude']:.1f}, {peak_row['longitude']:.1f}"
                if peak_row is not None
                else "",
            ),
            _kpi(
                "Highest SST anomaly",
                theme.format_number(anomaly, 2, " σ") if anomaly is not None else "—",
                "rolling baseline, not a climatology",
            ),
            _kpi(
                "Warm-spell cells",
                theme.format_count(warm_cells),
                "cells above +2σ — approximation",
            ),
            _kpi(
                "Sampled cells",
                theme.format_count(len(frame)),
                f"latest {theme.format_relative(int(frame['timestamp'].max()))}",
            ),
        ]

        severe = frame[frame["wave_height_m"] >= SEVERE_WAVE_HEIGHT_M].sort_values(
            "wave_height_m", ascending=False
        )
        if severe.empty:
            ticker = html.Div(
                f"No cell currently above {SEVERE_WAVE_HEIGHT_M:.1f} m.",
                className="op-kpi-sub",
            )
        else:
            ticker = [
                html.Div(
                    [
                        html.Div(
                            [
                                html.B(f"{row['wave_height_m']:.1f} m"),
                                html.Span(
                                    f"  {row['latitude']:.1f}, {row['longitude']:.1f}",
                                    style={"color": theme.TEXT_MUTED},
                                ),
                            ]
                        ),
                        html.Div(
                            f"{theme.format_number(row['wave_period_s'], 0, ' s')} · "
                            f"{theme.compass(row['wave_direction_deg'])}",
                            style={"color": theme.TEXT_MUTED, "fontSize": "12px"},
                        ),
                    ],
                    className="op-ticker-item",
                )
                for _, row in severe.head(40).iterrows()
            ]
        return kpis, ticker

    @app.callback(
        Output("pulse-map", "figure"),
        Input("pulse-refresh", "n_intervals"),
        Input("pulse-projection", "value"),
        Input("pulse-colour", "value"),
        Input("pulse-vectors", "value"),
    )
    def _map(_ticks: int, projection: str, colour_by: str, vectors: list[str]):
        frame = latest_grid_frame(services)
        if frame.empty or colour_by not in frame.columns:
            return theme.empty_figure(
                "No observations yet — the first ingestion cycle populates this map."
            )
        points = frame.dropna(subset=[colour_by])
        if points.empty:
            return theme.empty_figure(f"No cells currently report {colour_by}.")

        labels = {
            "sst_celsius": "Sea surface temperature (°C)",
            "wave_power_kw_m": "Wave power (kW/m)",
            "wave_height_m": "Wave height (m)",
        }
        scale = theme.SST_SCALE if colour_by == "sst_celsius" else theme.WAVE_SCALE

        data: list[dict[str, Any]] = []

        if vectors and "on" in vectors:
            data.append(_current_vector_trace(frame))

        data.append(
            {
                "type": "scattergeo",
                "lat": points["latitude"],
                "lon": points["longitude"],
                "mode": "markers",
                "marker": {
                    "size": 9,
                    "color": points[colour_by],
                    "colorscale": scale,
                    "showscale": True,
                    "colorbar": {
                        "title": {"text": labels.get(colour_by, colour_by), "side": "right"},
                        "thickness": 12,
                        "len": 0.7,
                        "outlinewidth": 0,
                    },
                    "line": {"width": 0.5, "color": "rgba(255,255,255,0.35)"},
                },
                "customdata": np.stack(
                    [
                        points["wave_height_m"].fillna(-1),
                        points["wave_period_s"].fillna(-1),
                        points["current_velocity_kmh"].fillna(-1),
                        points["sst_celsius"].fillna(-999),
                    ],
                    axis=-1,
                ),
                "hovertemplate": (
                    "<b>%{lat:.2f}, %{lon:.2f}</b><br>"
                    "Wave height %{customdata[0]:.2f} m<br>"
                    "Wave period %{customdata[1]:.1f} s<br>"
                    "Current %{customdata[2]:.1f} km/h<br>"
                    "SST %{customdata[3]:.2f} °C<extra></extra>"
                ),
                "name": "",
            }
        )

        layout = theme.figure_layout(
            margin={"l": 0, "r": 0, "t": 6, "b": 0},
            geo={
                "projection": {"type": projection},
                "showland": True,
                # Land has to read as clearly different from sea, or a viewer
                # cannot tell whether a sample sits somewhere plausible.
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
            showlegend=False,
        )
        layout.pop("xaxis", None)
        layout.pop("yaxis", None)
        return {"data": data, "layout": layout}

    @app.callback(
        Output("pulse-distribution", "figure"),
        Input("pulse-refresh", "n_intervals"),
    )
    def _distribution(_ticks: int):
        frame = latest_grid_frame(services)
        if frame.empty:
            return theme.empty_figure("No observations yet.")
        power = frame["wave_power_kw_m"].dropna()
        if power.empty:
            return theme.empty_figure("Wave power needs both height and period.")
        return {
            "data": [
                {
                    "type": "histogram",
                    "x": power,
                    "nbinsx": 40,
                    "marker": {"color": theme.ACCENT, "line": {"width": 0}},
                    "hovertemplate": "%{y} cells at %{x:.0f} kW/m<extra></extra>",
                }
            ],
            "layout": theme.figure_layout(
                title={"text": "Wave power distribution across sampled cells", "x": 0.01,
                       "font": {"size": 13}},
                xaxis={"title": "Wave power (kW/m)", "gridcolor": "rgba(255,255,255,0.07)"},
                yaxis={"title": "Cells", "gridcolor": "rgba(255,255,255,0.07)"},
                bargap=0.04,
            ),
        }


def _current_vector_trace(frame: pd.DataFrame) -> dict[str, Any]:
    """Ocean current arrows drawn as short great-circle segments.

    Plotly has no vector-field trace for geographic axes, so each arrow is a
    two-point line with a None separator between arrows - one trace for the
    whole field rather than hundreds of shapes, which keeps the map fast.
    """
    subset = frame.dropna(subset=["current_velocity_kmh", "current_direction_deg"])
    lats: list[float | None] = []
    lons: list[float | None] = []
    if subset.empty:
        return {"type": "scattergeo", "lat": [], "lon": [], "mode": "lines", "showlegend": False}

    peak = max(1e-6, float(subset["current_velocity_kmh"].max()))
    for _, row in subset.iterrows():
        speed = float(row["current_velocity_kmh"])
        # Oceanographic convention: direction is where the flow is going.
        bearing = np.radians(float(row["current_direction_deg"]))
        length = 1.0 + 5.0 * (speed / peak)
        lat0, lon0 = float(row["latitude"]), float(row["longitude"])
        lat1 = lat0 + length * np.cos(bearing)
        lon1 = lon0 + length * np.sin(bearing) / max(0.2, np.cos(np.radians(lat0)))
        lats.extend([lat0, lat1, None])
        lons.extend([lon0, lon1, None])

    return {
        "type": "scattergeo",
        "lat": lats,
        "lon": lons,
        "mode": "lines",
        "line": {"width": 1.1, "color": "rgba(120,220,255,0.5)"},
        "hoverinfo": "skip",
        "showlegend": False,
        "name": "currents",
    }
