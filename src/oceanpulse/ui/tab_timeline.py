"""Tab 2 — Port & Coastal Timeline.

Selecting a port does three things, in this order, and the order matters:

1. Resolve the port to a cell the marine model actually covers. Harbour
   coordinates routinely sit in a land cell, where every value is null.
2. Register it as tracked, so the daemon keeps its series current.
3. Backfill history from whichever sources can serve the requested window.

The status line reports what each source actually did. A chart that is empty
because sea level data stops in March is a different situation from one that
is empty because the fetch failed, and the interface says which.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from dash import Input, Output, State, dcc, html, no_update

from ..ingest.historical import backfill_port
from ..logging_setup import get_logger
from ..math_engine import downsample_frame, wave_energy_flux_series
from ..storage.base import ObservationFilter
from . import theme
from .place_search import place_search, register_place_search
from .services import Services

log = get_logger(__name__)


def layout(services: Services) -> html.Div:
    today = dt.date.today()
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            place_search("timeline"),
                            html.Div(
                                [
                                    html.Label("Date range"),
                                    dcc.DatePickerRange(
                                        id="timeline-dates",
                                        min_date_allowed=dt.date(1981, 9, 1),
                                        max_date_allowed=today + dt.timedelta(days=7),
                                        start_date=today - dt.timedelta(days=365),
                                        end_date=today,
                                        display_format="YYYY-MM-DD",
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label("Sources"),
                                    dcc.Checklist(
                                        id="timeline-sources",
                                        options=[
                                            {"label": " Deep archive (ERDDAP)", "value": "erddap"},
                                            {"label": " Include forecast", "value": "forecast"},
                                        ],
                                        value=["erddap"],
                                        inputStyle={"marginRight": "5px"},
                                        labelStyle={"display": "block", "marginBottom": "3px"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label(" "),
                                    html.Button(
                                        "Load timeline",
                                        id="timeline-load",
                                        className="op-button",
                                        n_clicks=0,
                                    ),
                                ],
                                className="op-field",
                            ),
                        ],
                        className="op-row",
                    ),
                    dcc.Loading(
                        html.Div(id="timeline-status", style={"marginTop": "12px"}),
                        type="dot",
                        color=theme.ACCENT,
                    ),
                ],
                className="op-card",
            ),
            html.Div(style={"height": "14px"}),
            html.Div(id="timeline-kpis", className="op-grid op-kpis"),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    dcc.Graph(
                        id="timeline-dual",
                        config={"displayModeBar": False},
                        style={"height": "420px"},
                    )
                ],
                className="op-card",
            ),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Graph(
                                id="timeline-polar",
                                config={"displayModeBar": False},
                                style={"height": "420px"},
                            )
                        ],
                        className="op-card",
                        style={"flex": "1 1 420px"},
                    ),
                    html.Div(
                        [
                            dcc.Graph(
                                id="timeline-power",
                                config={"displayModeBar": False},
                                style={"height": "420px"},
                            )
                        ],
                        className="op-card",
                        style={"flex": "1 1 420px"},
                    ),
                ],
                style={"display": "flex", "gap": "14px", "flexWrap": "wrap"},
            ),
            dcc.Store(id="timeline-active-port"),
        ]
    )


# ---------------------------------------------------------------------------


def port_frame(
    services: Services,
    port_id: str,
    start: dt.date,
    end: dt.date,
    include_forecast: bool = False,
) -> pd.DataFrame:
    rows = services.storage.query_observations(
        ObservationFilter(
            start_ms=int(dt.datetime.combine(start, dt.time.min, dt.timezone.utc).timestamp() * 1000),
            end_ms=int(dt.datetime.combine(end, dt.time.max, dt.timezone.utc).timestamp() * 1000),
            port_id=port_id,
            include_forecast=include_forecast,
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
    return frame


def _status_line(report: dict[str, Any]) -> html.Div:
    """Say plainly what each source contributed."""
    friendly = {
        "open_meteo": "Waves & currents (Open-Meteo marine model)",
        "erddap_sst": "Sea temperature (NOAA OISST v2.1)",
        "erddap_sla": "Sea level & geostrophic currents (NOAA altimetry)",
    }
    items = []
    for key, info in report.get("sources", {}).items():
        status = info.get("status")
        if status == "fetched" and not info.get("rows"):
            # A successful request that returned nothing is not a success.
            # Reporting it in green next to "fetched 0 rows" invites the
            # reader to blame an empty chart on the chart.
            text = "no data at this location — the grid cell is masked here"
            colour = theme.WARNING
        elif status == "fetched":
            text = f"fetched {info.get('rows', 0):,} rows"
            colour = theme.SUCCESS
        elif status == "cached":
            text = "already held locally — no request made"
            colour = theme.TEXT_MUTED
        elif status == "out_of_range":
            text = info.get("message", "outside dataset coverage")
            colour = theme.WARNING
        else:
            text = info.get("message", "failed")
            colour = theme.DANGER
        items.append(
            html.Div(
                [
                    html.Span("● ", style={"color": colour}),
                    html.B(friendly.get(key, key)),
                    html.Span(f" — {text}", style={"color": theme.TEXT_MUTED}),
                ],
                style={"marginBottom": "3px", "fontSize": "13px"},
            )
        )
    return html.Div(items)


def register(app: Any, services: Services) -> None:
    register_place_search(app, services, "timeline")

    @app.callback(
        Output("timeline-status", "children"),
        Output("timeline-active-port", "data"),
        Input("timeline-load", "n_clicks"),
        State("timeline-selected-port", "data"),
        State("timeline-dates", "start_date"),
        State("timeline-dates", "end_date"),
        State("timeline-sources", "value"),
        prevent_initial_call=True,
    )
    def _load(_clicks: int, port: dict[str, Any] | None, start: str, end: str, sources: list[str]):
        if not port:
            return html.Div("Search for and select a port first.", className="op-note warn"), no_update

        start_date = dt.date.fromisoformat(str(start)[:10])
        end_date = dt.date.fromisoformat(str(end)[:10])
        if start_date > end_date:
            return html.Div("Start date is after end date.", className="op-note warn"), no_update

        record = dict(port)
        port_id = record["port_id"]

        # Resolve a cell the marine model can actually answer for.
        tracked = services.storage.get_tracked_port(port_id)
        if tracked and tracked.get("marine_latitude") is not None:
            record["marine_latitude"] = tracked["marine_latitude"]
            record["marine_longitude"] = tracked["marine_longitude"]
        else:
            try:
                from ..ingest.open_meteo import OpenMeteoMarine

                marine = OpenMeteoMarine(services.client)
                cell = services.run_async(
                    marine.find_marine_cell(record["latitude"], record["longitude"]),
                    timeout=120.0,
                )
            except Exception as exc:  # noqa: BLE001 - report, do not crash a callback
                log.warning("marine cell lookup failed for %s: %s", port_id, exc)
                cell = None
            if cell is None:
                return (
                    html.Div(
                        f"{record['port_name']} has no marine model coverage within 1° — "
                        "it may be too far inland or in an enclosed water body.",
                        className="op-note warn",
                    ),
                    no_update,
                )
            record["marine_latitude"], record["marine_longitude"] = cell

        services.storage.add_tracked_port(record)

        try:
            report = services.run_async(
                backfill_port(
                    services.config,
                    services.storage,
                    services.client,
                    record,
                    start_date,
                    end_date,
                    include_erddap="erddap" in (sources or []),
                ),
                timeout=600.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("backfill failed for %s", port_id)
            return (
                html.Div(f"Backfill failed: {exc}", className="op-note warn"),
                {"port_id": port_id, "name": record["port_name"]},
            )

        offset = ""
        if (
            abs(record["marine_latitude"] - record["latitude"]) > 0.01
            or abs(record["marine_longitude"] - record["longitude"]) > 0.01
        ):
            offset = (
                f" Marine data is read from {record['marine_latitude']:.2f}, "
                f"{record['marine_longitude']:.2f} — the nearest cell with model coverage."
            )

        return (
            html.Div(
                [
                    html.Div(
                        f"{record['port_name']} is now tracked; the daemon will keep it "
                        f"current.{offset}",
                        style={"marginBottom": "8px"},
                    ),
                    _status_line(report),
                ],
                className="op-note",
            ),
            {"port_id": port_id, "name": record["port_name"]},
        )

    @app.callback(
        Output("timeline-kpis", "children"),
        Output("timeline-dual", "figure"),
        Output("timeline-polar", "figure"),
        Output("timeline-power", "figure"),
        Input("timeline-active-port", "data"),
        Input("timeline-dates", "start_date"),
        Input("timeline-dates", "end_date"),
        Input("timeline-sources", "value"),
    )
    def _render(active: dict[str, Any] | None, start: str, end: str, sources: list[str]):
        blank = theme.empty_figure("Select a port and load its timeline.")
        if not active:
            return [], blank, blank, blank

        include_forecast = "forecast" in (sources or [])
        frame = port_frame(
            services,
            active["port_id"],
            dt.date.fromisoformat(str(start)[:10]),
            dt.date.fromisoformat(str(end)[:10]),
            include_forecast=include_forecast,
        )
        if frame.empty:
            message = theme.empty_figure("No stored observations for this port and period.")
            return [], message, message, message

        kpis = _timeline_kpis(frame, active["name"])
        return (
            kpis,
            _dual_axis_figure(frame, services.config.max_plot_points),
            _polar_figure(frame),
            _power_figure(frame, services.config.max_plot_points),
        )


def _timeline_kpis(frame: pd.DataFrame, name: str) -> list[html.Div]:
    def card(label: str, value: str, sub: str) -> html.Div:
        return html.Div(
            [
                html.Div(label, className="op-kpi-label"),
                html.Div(value, className="op-kpi-value"),
                html.Div(sub, className="op-kpi-sub"),
            ],
            className="op-card",
        )

    sst = frame["sst_celsius"].dropna()
    sla = frame["sea_level_anomaly_m"].dropna()
    waves = frame["wave_height_m"].dropna()
    power = frame["wave_power_kw_m"].dropna()

    return [
        card(
            "Observations",
            theme.format_count(len(frame)),
            f"{name} · {frame['time'].min():%Y-%m-%d} → {frame['time'].max():%Y-%m-%d}",
        ),
        card(
            "Mean sea temperature",
            theme.format_number(sst.mean(), 2, " °C") if not sst.empty else "—",
            f"range {sst.min():.1f}–{sst.max():.1f} °C" if not sst.empty else "no SST in range",
        ),
        card(
            "Sea level anomaly",
            theme.format_number(sla.mean() * 100 if not sla.empty else None, 1, " cm"),
            f"peak {sla.max() * 100:.1f} cm" if not sla.empty else "altimetry lags ~4 months",
        ),
        card(
            "Peak wave power",
            theme.format_number(power.max() if not power.empty else None, 1, " kW/m"),
            f"max wave height {waves.max():.1f} m" if not waves.empty else "no wave data",
        ),
    ]


def _dual_axis_figure(frame: pd.DataFrame, max_points: int) -> dict[str, Any]:
    sst = downsample_frame(
        frame.dropna(subset=["sst_celsius"]), "timestamp", "sst_celsius", max_points
    )
    sla = downsample_frame(
        frame.dropna(subset=["sea_level_anomaly_m"]),
        "timestamp",
        "sea_level_anomaly_m",
        max_points,
    )
    data: list[dict[str, Any]] = []
    if not sst.empty:
        data.append(
            {
                "type": "scatter",
                "x": sst["time"],
                "y": sst["sst_celsius"],
                "name": "Sea surface temperature",
                "mode": "lines",
                "line": {"color": theme.WARNING, "width": 1.6},
                "hovertemplate": "%{x|%Y-%m-%d %H:%M}<br>%{y:.2f} °C<extra></extra>",
            }
        )
    if not sla.empty:
        data.append(
            {
                "type": "scatter",
                "x": sla["time"],
                "y": sla["sea_level_anomaly_m"] * 100.0,
                "name": "Sea level anomaly",
                "mode": "lines",
                "yaxis": "y2",
                "line": {"color": theme.ACCENT_ALT, "width": 1.6},
                "hovertemplate": "%{x|%Y-%m-%d}<br>%{y:.1f} cm<extra></extra>",
            }
        )
    if not data:
        return theme.empty_figure("Neither sea temperature nor sea level is available here.")

    return {
        "data": data,
        "layout": theme.figure_layout(
            title={"text": "Sea temperature and sea level anomaly", "x": 0.01,
                   "font": {"size": 13}},
            xaxis={"title": "", "gridcolor": "rgba(255,255,255,0.07)"},
            yaxis={
                "title": "SST (°C)",
                "gridcolor": "rgba(255,255,255,0.07)",
                "titlefont": {"color": theme.WARNING},
                "tickfont": {"color": theme.WARNING},
            },
            yaxis2={
                "title": "Sea level anomaly (cm)",
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "titlefont": {"color": theme.ACCENT_ALT},
                "tickfont": {"color": theme.ACCENT_ALT},
            },
            hovermode="x unified",
        ),
    }


def _polar_figure(frame: pd.DataFrame) -> dict[str, Any]:
    subset = frame.dropna(subset=["wave_direction_deg", "wave_period_s"])
    if subset.empty:
        return theme.empty_figure("No directional wave data in range.")
    if len(subset) > 4000:
        subset = subset.sample(4000, random_state=0)
    return {
        "data": [
            {
                "type": "scatterpolar",
                "r": subset["wave_period_s"],
                "theta": subset["wave_direction_deg"],
                "mode": "markers",
                "marker": {
                    "size": 5,
                    "opacity": 0.55,
                    "color": subset["wave_height_m"].fillna(0),
                    "colorscale": theme.WAVE_SCALE,
                    "showscale": True,
                    "colorbar": {"title": {"text": "Hs (m)"}, "thickness": 10, "len": 0.6,
                                 "outlinewidth": 0},
                },
                "hovertemplate": "Period %{r:.1f} s<br>Direction %{theta:.0f}°<extra></extra>",
            }
        ],
        "layout": theme.figure_layout(
            title={"text": "Swell direction and period", "x": 0.01, "font": {"size": 13}},
            polar={
                "bgcolor": "rgba(255,255,255,0.02)",
                "radialaxis": {
                    "title": {"text": "Period (s)"},
                    "gridcolor": "rgba(255,255,255,0.12)",
                    "linecolor": "rgba(255,255,255,0.12)",
                },
                "angularaxis": {
                    "direction": "clockwise",
                    "rotation": 90,
                    "gridcolor": "rgba(255,255,255,0.12)",
                    "linecolor": "rgba(255,255,255,0.12)",
                },
            },
            showlegend=False,
        ),
    }


def _power_figure(frame: pd.DataFrame, max_points: int) -> dict[str, Any]:
    subset = downsample_frame(
        frame.dropna(subset=["wave_power_kw_m"]), "timestamp", "wave_power_kw_m", max_points
    )
    if subset.empty:
        return theme.empty_figure("Wave power needs both height and period.")
    return {
        "data": [
            {
                "type": "scatter",
                "x": subset["time"],
                "y": subset["wave_power_kw_m"],
                "mode": "lines",
                "fill": "tozeroy",
                "line": {"color": theme.ACCENT, "width": 1.2},
                "fillcolor": "rgba(49,208,198,0.15)",
                "hovertemplate": "%{x|%Y-%m-%d %H:%M}<br>%{y:.1f} kW/m<extra></extra>",
            }
        ],
        "layout": theme.figure_layout(
            title={"text": "Wave energy flux", "x": 0.01, "font": {"size": 13}},
            xaxis={"title": "", "gridcolor": "rgba(255,255,255,0.07)"},
            yaxis={"title": "kW per metre of crest", "gridcolor": "rgba(255,255,255,0.07)"},
        ),
    }
