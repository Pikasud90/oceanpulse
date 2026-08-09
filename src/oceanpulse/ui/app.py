"""Dash application: layout, header controls, and tab wiring."""

from __future__ import annotations

from typing import Any

import dash
from dash import Input, Output, State, dcc, html

from ..config import ALLOWED_POLL_INTERVALS, Config
from ..logging_setup import get_logger
from . import tab_analytics, tab_export, tab_pulse, tab_timeline, theme
from .services import Services, init_services

log = get_logger(__name__)

INDEX_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  {%metas%}
  <title>{%title%}</title>
  {%favicon%}
  {%css%}
  <style>__STYLESHEET__</style>
</head>
<body>
  {%app_entry%}
  <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""


def build_app(config: Config | None = None) -> tuple[dash.Dash, Services]:
    services = init_services(config)

    app = dash.Dash(
        __name__,
        title="OceanPulse",
        update_title=None,
        # Tabs render lazily, so callbacks reference components that do not
        # exist until their tab is first opened.
        suppress_callback_exceptions=True,
        index_string=INDEX_TEMPLATE.replace("__STYLESHEET__", theme.STYLESHEET),
    )

    app.layout = html.Div(
        [
            dcc.Interval(id="header-refresh", interval=15_000, n_intervals=0),
            html.Div(
                [
                    _header(services),
                    dcc.Tabs(
                        id="op-tabs",
                        value="pulse",
                        parent_className="tab-container",
                        className="op-tabs",
                        children=[
                            dcc.Tab(label="Global Pulse", value="pulse",
                                    className="op-tab", selected_className="op-tab--selected"),
                            dcc.Tab(label="Port Timeline", value="timeline",
                                    className="op-tab", selected_className="op-tab--selected"),
                            dcc.Tab(label="Physics & Correlation", value="analytics",
                                    className="op-tab", selected_className="op-tab--selected"),
                            dcc.Tab(label="Data Export", value="export",
                                    className="op-tab", selected_className="op-tab--selected"),
                        ],
                    ),
                    html.Div(id="op-tab-content", style={"marginTop": "18px"}),
                    _footer(),
                ],
                className="op-shell",
            ),
        ]
    )

    _register_shell(app, services)
    tab_pulse.register(app, services)
    tab_timeline.register(app, services)
    tab_analytics.register(app, services)
    tab_export.register(app, services)
    return app, services


def _header(services: Services) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.H1("OceanPulse"),
                    html.Span("self-hosted marine data engine"),
                ],
                className="op-brand",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Poll interval"),
                            dcc.Dropdown(
                                id="header-poll-interval",
                                options=[
                                    {"label": f"{m} minutes", "value": m}
                                    for m in ALLOWED_POLL_INTERVALS
                                ],
                                value=services.poll_interval(),
                                clearable=False,
                                style={"width": "150px", "color": "#0d2840"},
                            ),
                        ],
                        className="op-field",
                    ),
                    html.Div(id="header-status", className="op-status"),
                ],
                className="op-header-controls",
            ),
        ],
        className="op-header",
    )


def _footer() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    "Wave, current and near-term sea-surface temperature data from ",
                    html.A("Open-Meteo Marine", href="https://open-meteo.com/", target="_blank"),
                    " (CC BY 4.0) — these are ",
                    html.B("numerical model output, not buoy measurements"),
                    ". Historical sea temperature from NOAA OISST v2.1 and sea level "
                    "anomaly from NOAA CoastWatch altimetry, served via ",
                    html.A("ERDDAP", href="https://coastwatch.pfeg.noaa.gov/erddap/",
                           target="_blank"),
                    " (public domain). Port locations from the ",
                    html.A("NGA World Port Index",
                           href="https://msi.nga.mil/Publications/WPI", target="_blank"),
                    " (public domain); coastal place names from ",
                    html.A("GeoNames", href="https://www.geonames.org/", target="_blank"),
                    " (CC BY 4.0).",
                ]
            ),
            html.Div(
                "For research and education. Not a navigational aid and not an emergency "
                "warning system. Consult your national hydrographic or meteorological "
                "service for operational marine forecasts.",
                style={"marginTop": "6px"},
            ),
        ],
        className="op-footer",
    )


def _register_shell(app: dash.Dash, services: Services) -> None:
    @app.callback(Output("op-tab-content", "children"), Input("op-tabs", "value"))
    def _render_tab(tab: str):
        if tab == "timeline":
            return tab_timeline.layout(services)
        if tab == "analytics":
            return tab_analytics.layout(services)
        if tab == "export":
            return tab_export.layout(services)
        return tab_pulse.layout(services)

    @app.callback(
        Output("header-status", "children"),
        Input("header-refresh", "n_intervals"),
    )
    def _status(_ticks: int):
        summary = services.status_summary()
        status = summary.get("daemon_status", "stopped")
        return [
            html.Div([html.B(theme.format_count(summary["total_observations"])), " observations"]),
            html.Div([html.B(theme.format_count(summary["valid_grid_points"])), " grid cells"]),
            html.Div([html.B(theme.format_count(summary["tracked_ports"])), " tracked ports"]),
            html.Div([html.B(theme.format_bytes(summary["database_bytes"])), " on disk"]),
            html.Div(
                theme.STATUS_LABELS.get(status, status),
                title=summary.get("daemon_detail", ""),
                style={"color": theme.STATUS_COLOURS.get(status, theme.TEXT_MUTED),
                       "fontWeight": 600},
            ),
        ]

    @app.callback(
        Output("header-poll-interval", "value"),
        Input("header-poll-interval", "value"),
        prevent_initial_call=True,
    )
    def _set_interval(minutes: int):
        if minutes in ALLOWED_POLL_INTERVALS:
            services.set_poll_interval(minutes)
            log.info("poll interval set to %d minutes", minutes)
        return minutes
