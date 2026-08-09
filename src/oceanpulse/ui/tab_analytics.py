"""Tab 3 — Physics & Correlation Engine.

The inundation panel needs a word of warning, and it carries one in the
interface too. OceanPulse has **no tide data**. What it can show is a still-
water proxy assembled from sea level anomaly, an optional user-set surge
allowance, and a crude wave-setup term. That is an illustration of how those
components stack against a threshold, not a flood forecast, and the tab says
so where a user will actually read it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Sequence

import numpy as np
import pandas as pd
from dash import Input, Output, dcc, html

from ..logging_setup import get_logger
from ..math_engine import correlation_matrix, wave_energy_flux_series
from ..storage.base import ObservationFilter
from . import theme
from .services import Services

log = get_logger(__name__)

CORRELATION_COLUMNS = {
    "sst_celsius": "Sea temperature",
    "sea_level_anomaly_m": "Sea level anomaly",
    "wave_height_m": "Wave height",
    "wave_period_s": "Wave period",
    "wave_power_kw_m": "Wave power",
    "current_velocity_kmh": "Current speed",
}

# Wave setup at the shore is roughly a fifth of offshore significant height.
# A real figure depends on beach slope, which we do not have.
WAVE_SETUP_FRACTION = 0.2


def layout(services: Services) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Scope"),
                                    dcc.Dropdown(
                                        id="analytics-scope",
                                        options=[],
                                        value="__global__",
                                        clearable=False,
                                        style={"width": "320px", "color": "#0d2840"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label("Window"),
                                    dcc.Dropdown(
                                        id="analytics-window",
                                        options=[
                                            {"label": "Last 30 days", "value": 30},
                                            {"label": "Last 90 days", "value": 90},
                                            {"label": "Last year", "value": 365},
                                            {"label": "Last 5 years", "value": 1825},
                                            {"label": "Everything held", "value": 0},
                                        ],
                                        value=365,
                                        clearable=False,
                                        style={"width": "200px", "color": "#0d2840"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(id="analytics-summary", className="op-kpi-sub",
                                     style={"alignSelf": "flex-end"}),
                        ],
                        className="op-row",
                    )
                ],
                className="op-card",
            ),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Graph(id="analytics-correlation", config={"displayModeBar": False},
                                      style={"height": "440px"})
                        ],
                        className="op-card",
                        style={"flex": "1 1 460px"},
                    ),
                    html.Div(
                        [
                            dcc.Graph(id="analytics-spectrum", config={"displayModeBar": False},
                                      style={"height": "440px"})
                        ],
                        className="op-card",
                        style={"flex": "1 1 460px"},
                    ),
                ],
                style={"display": "flex", "gap": "14px", "flexWrap": "wrap"},
            ),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    html.Div("Coastal exceedance model", style={"fontWeight": 650,
                                                                "marginBottom": "4px"}),
                    html.Div(
                        "Illustrative only. OceanPulse holds no tide predictions, so this "
                        "stacks sea level anomaly, a surge allowance you choose, and a crude "
                        "wave-setup term against a threshold height. It is not a flood "
                        "forecast and must not be used for any life-safety decision.",
                        className="op-note warn",
                        style={"marginBottom": "14px"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Threshold height above mean sea level (m)"),
                                    dcc.Slider(
                                        id="analytics-threshold",
                                        min=0.0,
                                        max=3.0,
                                        step=0.05,
                                        value=0.5,
                                        marks={i: f"{i} m" for i in range(0, 4)},
                                        tooltip={"placement": "bottom", "always_visible": True},
                                    ),
                                ],
                                style={"flex": "1 1 380px"},
                            ),
                            html.Div(
                                [
                                    html.Label("Storm surge allowance (m)"),
                                    dcc.Slider(
                                        id="analytics-surge",
                                        min=0.0,
                                        max=2.0,
                                        step=0.05,
                                        value=0.0,
                                        marks={i: f"{i} m" for i in range(0, 3)},
                                        tooltip={"placement": "bottom", "always_visible": True},
                                    ),
                                ],
                                style={"flex": "1 1 380px"},
                            ),
                        ],
                        style={"display": "flex", "gap": "26px", "flexWrap": "wrap"},
                    ),
                    dcc.Graph(id="analytics-inundation", config={"displayModeBar": False},
                              style={"height": "360px", "marginTop": "10px"}),
                ],
                className="op-card",
            ),
        ]
    )


def _scoped_frame(services: Services, scope: str, days: int) -> pd.DataFrame:
    end = dt.datetime.now(dt.timezone.utc)
    start_ms = None if not days else int((end - dt.timedelta(days=days)).timestamp() * 1000)
    filters = ObservationFilter(
        start_ms=start_ms,
        end_ms=int(end.timestamp() * 1000),
        port_id=None if scope == "__global__" else scope,
        include_forecast=False,
        order="asc",
        limit=400_000,
    )
    rows = services.storage.query_observations(filters)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["time"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame["wave_power_kw_m"] = wave_energy_flux_series(
        frame.get("wave_height_m", pd.Series(dtype=float)),
        frame.get("wave_period_s", pd.Series(dtype=float)),
    )
    return frame


def daily_alignment(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Collapse everything to daily means before correlating.

    The sources do not share a clock. Open-Meteo is hourly, OISST is stamped
    at 12:00 UTC, and the altimetry product at 00:00 - so no row ever carries
    both a sea temperature and a sea level, every pairwise overlap is empty,
    and the matrix comes back entirely null. That reads as "these quantities
    are unrelated" when it actually means "these were never compared".

    A daily mean is also the honest resolution: correlating an hourly wave
    height against a daily altimetry pass at hourly granularity would imply a
    precision the sea level series does not have.
    """
    if frame.empty or "time" not in frame.columns:
        return frame
    present = [c for c in columns if c in frame.columns]
    if not present:
        return frame
    working = frame[["time", *present]].copy()
    working["day"] = working["time"].dt.floor("1D")
    return working.groupby("day", as_index=False)[present].mean()


def register(app: Any, services: Services) -> None:
    @app.callback(
        Output("analytics-scope", "options"),
        Input("analytics-window", "value"),
    )
    def _scopes(_window: int):
        options = [{"label": "Global — all sampled cells", "value": "__global__"}]
        for port in services.storage.get_tracked_ports():
            options.append(
                {"label": f"{port['port_name']} ({port['country_code']})", "value": port["port_id"]}
            )
        return options

    @app.callback(
        Output("analytics-correlation", "figure"),
        Output("analytics-spectrum", "figure"),
        Output("analytics-summary", "children"),
        Input("analytics-scope", "value"),
        Input("analytics-window", "value"),
    )
    def _analytics(scope: str, window: int):
        frame = _scoped_frame(services, scope, window)
        if frame.empty:
            blank = theme.empty_figure("No observations in this scope yet.")
            return blank, blank, "no data"

        available = [c for c in CORRELATION_COLUMNS if c in frame.columns]
        matrix = correlation_matrix(daily_alignment(frame, available), available)
        if matrix.empty:
            correlation = theme.empty_figure(
                "Correlation needs at least two variables with data. "
                "Track a port and load its timeline to bring in sea level."
            )
        else:
            labels = [CORRELATION_COLUMNS[c] for c in matrix.columns]
            correlation = {
                "data": [
                    {
                        "type": "heatmap",
                        "z": matrix.values,
                        "x": labels,
                        "y": labels,
                        "zmin": -1,
                        "zmax": 1,
                        "colorscale": "RdBu",
                        "reversescale": True,
                        "text": np.round(matrix.values, 2),
                        "texttemplate": "%{text}",
                        "textfont": {"size": 11},
                        "hovertemplate": "%{y} vs %{x}<br>r = %{z:.3f}<extra></extra>",
                        "colorbar": {"title": {"text": "Pearson r"}, "thickness": 12,
                                     "outlinewidth": 0},
                    }
                ],
                "layout": theme.figure_layout(
                    title={"text": "Correlation between marine variables (daily means)", "x": 0.01,
                           "font": {"size": 13}},
                    margin={"l": 130, "r": 20, "t": 46, "b": 110},
                    xaxis={"tickangle": -35, "gridcolor": "rgba(0,0,0,0)"},
                    yaxis={"gridcolor": "rgba(0,0,0,0)"},
                ),
            }

        power = frame["wave_power_kw_m"].dropna()
        if power.empty:
            spectrum = theme.empty_figure("Wave power needs both height and period.")
        else:
            counts, edges = np.histogram(power, bins=50, density=True)
            centres = (edges[:-1] + edges[1:]) / 2.0
            spectrum = {
                "data": [
                    {
                        "type": "histogram",
                        "x": power,
                        "nbinsx": 50,
                        "histnorm": "probability density",
                        "name": "observed",
                        "marker": {"color": "rgba(49,208,198,0.55)"},
                        "hovertemplate": "%{x:.0f} kW/m<extra></extra>",
                    },
                    {
                        "type": "scatter",
                        "x": centres,
                        "y": counts,
                        "mode": "lines",
                        "name": "density",
                        "line": {"color": theme.WARNING, "width": 2, "shape": "spline"},
                        "hoverinfo": "skip",
                    },
                ],
                "layout": theme.figure_layout(
                    title={
                        "text": f"Wave power spectrum · median "
                                f"{power.median():.1f} kW/m, 99th pct {power.quantile(0.99):.0f}",
                        "x": 0.01,
                        "font": {"size": 13},
                    },
                    xaxis={"title": "Wave power (kW/m)", "gridcolor": "rgba(255,255,255,0.07)"},
                    yaxis={"title": "Probability density", "gridcolor": "rgba(255,255,255,0.07)"},
                    bargap=0.03,
                ),
            }

        summary = (
            f"{len(frame):,} observations · "
            f"{frame['time'].min():%Y-%m-%d} → {frame['time'].max():%Y-%m-%d}"
        )
        return correlation, spectrum, summary

    @app.callback(
        Output("analytics-inundation", "figure"),
        Input("analytics-scope", "value"),
        Input("analytics-window", "value"),
        Input("analytics-threshold", "value"),
        Input("analytics-surge", "value"),
    )
    def _inundation(scope: str, window: int, threshold: float, surge: float):
        frame = _scoped_frame(services, scope, window)
        if frame.empty:
            return theme.empty_figure("No observations in this scope yet.")

        subset = frame.dropna(subset=["sea_level_anomaly_m"]).copy()
        if subset.empty:
            return theme.empty_figure(
                "No sea level anomaly held for this scope. Load a port timeline with the "
                "deep archive enabled — altimetry covers 2017 onwards."
            )

        setup = subset["wave_height_m"].fillna(0.0) * WAVE_SETUP_FRACTION
        subset["water_level_m"] = subset["sea_level_anomaly_m"] + setup + float(surge or 0.0)

        daily = subset.set_index("time")["water_level_m"].resample("1D").max().dropna()
        if daily.empty:
            return theme.empty_figure("Not enough sea level data to resample.")

        exceed = daily >= float(threshold)
        colours = [theme.DANGER if flag else theme.ACCENT_ALT for flag in exceed]
        share = 100.0 * exceed.mean()

        return {
            "data": [
                {
                    "type": "bar",
                    "x": daily.index,
                    "y": daily.values,
                    "marker": {"color": colours},
                    "hovertemplate": "%{x|%Y-%m-%d}<br>%{y:.3f} m<extra></extra>",
                    "name": "daily maximum",
                },
                {
                    "type": "scatter",
                    "x": [daily.index.min(), daily.index.max()],
                    "y": [threshold, threshold],
                    "mode": "lines",
                    "line": {"color": theme.WARNING, "width": 2, "dash": "dash"},
                    "name": f"threshold {threshold:.2f} m",
                    "hoverinfo": "skip",
                },
            ],
            "layout": theme.figure_layout(
                title={
                    "text": f"Daily maximum still-water proxy · {exceed.sum()} of "
                            f"{len(daily)} days at or above threshold ({share:.1f}%)",
                    "x": 0.01,
                    "font": {"size": 13},
                },
                xaxis={"title": "", "gridcolor": "rgba(255,255,255,0.07)"},
                yaxis={"title": "Sea level anomaly + setup + surge (m)",
                       "gridcolor": "rgba(255,255,255,0.07)"},
                showlegend=False,
                bargap=0.1,
            ),
        }
