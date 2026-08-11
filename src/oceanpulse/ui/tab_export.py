"""Tab 4 — Machine Learning Data Export.

Also where dataset *definitions* are saved. A saved dataset stores the
specification, not a copy of the rows: re-running it later picks up everything
ingested since, which is what you want from a dataset you intend to retrain
on. Storing a frozen extract would quietly go stale.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from dash import Input, Output, State, dcc, html, no_update

from ..exporting.aggregate import (
    build_dataset,
    suggest_filename,
    to_csv_bytes,
    to_parquet_bytes,
)
from ..exporting.manifest import build_bundle, dictionary_for
from ..logging_setup import get_logger
from ..storage.base import BoundingBox
from . import theme
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
                            html.Div(
                                [
                                    html.Label("Spatial scope"),
                                    dcc.RadioItems(
                                        id="export-scope-mode",
                                        options=[
                                            {"label": " Global", "value": "global"},
                                            {"label": " Tracked port", "value": "port"},
                                            {"label": " Bounding box", "value": "bbox"},
                                        ],
                                        value="global",
                                        inline=True,
                                        inputStyle={"marginRight": "5px"},
                                        labelStyle={"marginRight": "14px"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label("Date range"),
                                    dcc.DatePickerRange(
                                        id="export-dates",
                                        min_date_allowed=dt.date(1981, 9, 1),
                                        max_date_allowed=today + dt.timedelta(days=7),
                                        start_date=today - dt.timedelta(days=90),
                                        end_date=today,
                                        display_format="YYYY-MM-DD",
                                    ),
                                ],
                                className="op-field",
                            ),
                        ],
                        className="op-row",
                    ),
                    html.Div(
                        id="export-port-row",
                        children=[
                            html.Div(
                                [
                                    html.Label("Tracked port"),
                                    dcc.Dropdown(
                                        id="export-port",
                                        options=[],
                                        placeholder="Select a tracked port",
                                        style={"width": "340px", "color": "#0d2840"},
                                    ),
                                ],
                                className="op-field",
                            )
                        ],
                        className="op-row",
                        style={"display": "none", "marginTop": "10px"},
                    ),
                    html.Div(
                        id="export-bbox-row",
                        children=[
                            html.Div(
                                [html.Label(label), dcc.Input(id=input_id, type="number",
                                                              value=value, className="op-input",
                                                              style={"width": "120px"})],
                                className="op-field",
                            )
                            for label, input_id, value in (
                                ("Min latitude", "export-min-lat", -90),
                                ("Max latitude", "export-max-lat", 90),
                                ("Min longitude", "export-min-lon", -180),
                                ("Max longitude", "export-max-lon", 180),
                            )
                        ],
                        className="op-row",
                        style={"display": "none", "marginTop": "10px"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Output shape"),
                                    dcc.Dropdown(
                                        id="export-mode",
                                        options=[
                                            {"label": "Aggregated time series", "value": "aggregated"},
                                            {"label": "Raw observation log", "value": "raw"},
                                        ],
                                        value="aggregated",
                                        clearable=False,
                                        style={"width": "230px", "color": "#0d2840"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label("Interval"),
                                    dcc.Dropdown(
                                        id="export-interval",
                                        options=[
                                            {"label": "1 hour", "value": "1h"},
                                            {"label": "6 hours", "value": "6h"},
                                            {"label": "1 day", "value": "1d"},
                                            {"label": "1 week", "value": "1w"},
                                        ],
                                        value="1d",
                                        clearable=False,
                                        style={"width": "150px", "color": "#0d2840"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label("Gaps in intensive columns"),
                                    dcc.Dropdown(
                                        id="export-fill",
                                        options=[
                                            {"label": "Leave empty (NaN)", "value": "none"},
                                            {"label": "Forward fill", "value": "ffill"},
                                            {"label": "Linear interpolation", "value": "interpolate"},
                                            {"label": "Zero fill", "value": "zero"},
                                        ],
                                        value="none",
                                        clearable=False,
                                        style={"width": "230px", "color": "#0d2840"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label("Extra columns"),
                                    dcc.Checklist(
                                        id="export-forecast",
                                        options=[
                                            {"label": " Forecast rows", "value": "yes"},
                                        ],
                                        value=[],
                                        inputStyle={"marginRight": "5px"},
                                        labelStyle={"display": "block"},
                                    ),
                                    dcc.Checklist(
                                        id="export-derived",
                                        options=[
                                            {"label": " Rolling z-scores + log energy",
                                             "value": "yes"},
                                        ],
                                        value=[],
                                        inputStyle={"marginRight": "5px"},
                                        labelStyle={"display": "block"},
                                    ),
                                ],
                                className="op-field",
                            ),
                            html.Div(
                                [
                                    html.Label(" "),
                                    html.Button("Load data", id="export-load",
                                                className="op-button", n_clicks=0),
                                ],
                                className="op-field",
                            ),
                        ],
                        className="op-row",
                        style={"marginTop": "10px"},
                    ),
                    html.Div(
                        "Counts and summed energy always fill quiet windows with zero — for "
                        "them zero is the true value. The choice above applies only to "
                        "intensive columns like temperature and wave height, where zero would "
                        "be a measurement rather than a gap.",
                        className="op-note",
                        style={"marginTop": "12px"},
                    ),
                ],
                className="op-card",
            ),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    html.Div(
                        [
                            html.Button("Download bundle (.zip)", id="export-bundle",
                                        className="op-button", n_clicks=0,
                                        title="Parquet + manifest + data dictionary + README"),
                            html.Button("CSV only", id="export-csv",
                                        className="op-button secondary", n_clicks=0,
                                        style={"marginLeft": "8px"}),
                            html.Button("Parquet only", id="export-parquet",
                                        className="op-button secondary", n_clicks=0,
                                        style={"marginLeft": "8px"}),
                            dcc.Input(id="export-save-name", type="text",
                                      placeholder="Name this dataset…", className="op-input",
                                      style={"marginLeft": "18px", "width": "230px"}),
                            html.Button("Save definition", id="export-save",
                                        className="op-button", n_clicks=0,
                                        style={"marginLeft": "8px"}),
                        ],
                        className="op-row",
                    ),
                    dcc.Loading(
                        html.Div(id="export-status", style={"marginTop": "12px"}),
                        type="dot",
                        color=theme.ACCENT,
                    ),
                    html.Div(
                        "The bundle is the recommended download: it carries the "
                        "rows plus a manifest, a data dictionary giving every "
                        "column's unit, provider and whether it was measured, "
                        "modelled or derived, and a plain-text README with the "
                        "caveats. Six months from now that is the difference "
                        "between a dataset and a pile of numbers.",
                        className="op-note",
                        style={"marginTop": "12px"},
                    ),
                    html.Div(id="export-preview", className="op-scroll",
                             style={"marginTop": "12px"}),
                    dcc.Download(id="export-download"),
                ],
                className="op-card",
            ),
            html.Div(className="op-spacer"),
            html.Div(
                [
                    html.Div("Data dictionary", className="op-card-title"),
                    html.Div(
                        "What each column in the current result means. Ships "
                        "inside the bundle as data_dictionary.csv.",
                        className="op-card-sub",
                    ),
                    html.Div(id="export-dictionary", className="op-scroll"),
                ],
                className="op-card",
            ),
            html.Div(style={"height": "14px"}),
            html.Div(
                [
                    html.Div("Saved datasets", style={"fontWeight": 650, "marginBottom": "8px"}),
                    html.Div(
                        "A saved dataset stores the query, not the rows, so re-running it "
                        "picks up everything ingested since.",
                        className="op-kpi-sub",
                        style={"marginBottom": "10px"},
                    ),
                    html.Div(id="export-saved-list"),
                ],
                className="op-card",
            ),
            dcc.Store(id="export-spec"),
        ]
    )


def _collect_spec(
    scope_mode: str,
    port_id: str | None,
    bbox_values: tuple[Any, Any, Any, Any],
    start: str,
    end: str,
    mode: str,
    interval: str,
    fill: str,
    forecast: list[str],
    derived: list[str],
) -> dict[str, Any]:
    return {
        "scope_mode": scope_mode,
        "port_id": port_id,
        "min_lat": bbox_values[0],
        "max_lat": bbox_values[1],
        "min_lon": bbox_values[2],
        "max_lon": bbox_values[3],
        "start": str(start)[:10],
        "end": str(end)[:10],
        "mode": mode,
        "interval": interval,
        "fill": fill,
        "include_forecast": bool(forecast),
        "derived_features": bool(derived),
    }


def _run_spec(services: Services, spec: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    start = dt.datetime.combine(
        dt.date.fromisoformat(spec["start"]), dt.time.min, dt.timezone.utc
    )
    end = dt.datetime.combine(
        dt.date.fromisoformat(spec["end"]), dt.time.max, dt.timezone.utc
    )
    bbox = None
    port_id = None
    if spec["scope_mode"] == "bbox":
        bbox = BoundingBox(
            min_lat=float(spec.get("min_lat") if spec.get("min_lat") is not None else -90),
            max_lat=float(spec.get("max_lat") if spec.get("max_lat") is not None else 90),
            min_lon=float(spec.get("min_lon") if spec.get("min_lon") is not None else -180),
            max_lon=float(spec.get("max_lon") if spec.get("max_lon") is not None else 180),
        )
    elif spec["scope_mode"] == "port":
        port_id = spec.get("port_id")

    return build_dataset(
        services.storage,
        start=start,
        end=end,
        bbox=bbox,
        port_id=port_id,
        mode=spec.get("mode", "aggregated"),
        interval=spec.get("interval", "1d"),
        intensive_fill=spec.get("fill", "none"),
        include_forecast=spec.get("include_forecast", False),
        derived_features=spec.get("derived_features", False),
        max_rows=services.config.max_export_rows,
    )


def _preview_table(frame: pd.DataFrame, limit: int = 25) -> Any:
    if frame.empty:
        return html.Div("No rows matched.", className="op-kpi-sub")
    head = frame.head(limit)
    return html.Table(
        [
            html.Thead(html.Tr([html.Th(column) for column in head.columns])),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(
                                f"{value:.3f}" if isinstance(value, float) else str(value)[:26]
                            )
                            for value in row
                        ]
                    )
                    for row in head.itertuples(index=False)
                ]
            ),
        ],
        className="op-table",
    )


def _dictionary_table(frame: pd.DataFrame) -> Any:
    """Show the same dictionary that ships inside the bundle."""
    if frame.empty:
        return html.Div("Load a dataset to see its columns.", className="op-kpi-sub")
    table = dictionary_for(list(frame.columns))
    nature_colour = {
        "measurement": theme.SUCCESS,
        "analysis": theme.ACCENT,
        "model": theme.WARNING,
        "derived": theme.VIOLET,
        "metadata": theme.TEXT_MUTED,
    }
    return html.Table(
        [
            html.Thead(
                html.Tr([html.Th(h) for h in
                         ("Column", "Unit", "Nature", "Provider", "Meaning")])
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(row["column"]),
                            html.Td(row["unit"] or "—"),
                            html.Td(
                                row["nature"],
                                style={"color": nature_colour.get(row["nature"],
                                                                  theme.TEXT_MUTED),
                                       "fontWeight": 620},
                            ),
                            html.Td(row["provider"]),
                            html.Td(row["description"],
                                    style={"whiteSpace": "normal", "maxWidth": "460px"}),
                        ]
                    )
                    for _, row in table.iterrows()
                ]
            ),
        ],
        className="op-table",
    )


def register(app: Any, services: Services) -> None:
    @app.callback(
        Output("export-port-row", "style"),
        Output("export-bbox-row", "style"),
        Input("export-scope-mode", "value"),
    )
    def _toggle(scope_mode: str):
        hidden = {"display": "none"}
        shown = {"display": "flex", "marginTop": "10px", "gap": "14px", "flexWrap": "wrap"}
        return (
            shown if scope_mode == "port" else hidden,
            shown if scope_mode == "bbox" else hidden,
        )

    @app.callback(Output("export-port", "options"), Input("export-scope-mode", "value"))
    def _ports(_mode: str):
        return [
            {"label": f"{p['port_name']} ({p['country_code']})", "value": p["port_id"]}
            for p in services.storage.get_tracked_ports()
        ]

    @app.callback(
        Output("export-status", "children"),
        Output("export-preview", "children"),
        Output("export-dictionary", "children"),
        Output("export-spec", "data"),
        Input("export-load", "n_clicks"),
        State("export-scope-mode", "value"),
        State("export-port", "value"),
        State("export-min-lat", "value"),
        State("export-max-lat", "value"),
        State("export-min-lon", "value"),
        State("export-max-lon", "value"),
        State("export-dates", "start_date"),
        State("export-dates", "end_date"),
        State("export-mode", "value"),
        State("export-interval", "value"),
        State("export-fill", "value"),
        State("export-forecast", "value"),
        State("export-derived", "value"),
        prevent_initial_call=True,
    )
    def _load(_clicks, scope_mode, port_id, min_lat, max_lat, min_lon, max_lon,
              start, end, mode, interval, fill, forecast, derived):
        spec = _collect_spec(
            scope_mode, port_id, (min_lat, max_lat, min_lon, max_lon),
            start, end, mode, interval, fill, forecast, derived,
        )
        if scope_mode == "port" and not port_id:
            return (html.Div("Select a tracked port first.", className="op-note warn"),
                    None, None, None)
        try:
            frame, meta = _run_spec(services, spec)
        except Exception as exc:  # noqa: BLE001
            log.exception("export build failed")
            return (html.Div(f"Could not build dataset: {exc}", className="op-note warn"),
                    None, None, None)

        note = (
            f"{meta['rows']:,} rows from {meta['matched_observations']:,} observations · "
            f"{meta['mode']} · {meta['interval']}"
        )
        if meta.get("truncated"):
            note += " · truncated at the row cap"
        return (html.Div(note, className="op-note"), _preview_table(frame),
                _dictionary_table(frame), spec)

    @app.callback(
        Output("export-download", "data"),
        Input("export-csv", "n_clicks"),
        Input("export-parquet", "n_clicks"),
        Input("export-bundle", "n_clicks"),
        State("export-spec", "data"),
        prevent_initial_call=True,
    )
    def _download(csv_clicks, parquet_clicks, bundle_clicks, spec):
        from dash import callback_context

        if not spec:
            return no_update
        triggered = callback_context.triggered_id
        frame, meta = _run_spec(services, spec)
        if frame.empty:
            return no_update
        scope = spec.get("port_id") or spec.get("scope_mode") or "dataset"
        if triggered == "export-bundle":
            payload = build_bundle(
                frame, spec, meta,
                to_parquet_bytes(frame),
                suggest_filename(scope, "parquet"),
            )
            return dcc.send_bytes(payload, suggest_filename(scope, "zip"))
        if triggered == "export-parquet":
            return dcc.send_bytes(
                to_parquet_bytes(frame), suggest_filename(scope, "parquet")
            )
        return dcc.send_bytes(to_csv_bytes(frame), suggest_filename(scope, "csv"))

    @app.callback(
        Output("export-saved-list", "children"),
        Input("export-save", "n_clicks"),
        Input("export-load", "n_clicks"),
        State("export-save-name", "value"),
        State("export-spec", "data"),
    )
    def _saved(save_clicks, _load_clicks, name, spec):
        from dash import callback_context

        triggered = callback_context.triggered_id
        if triggered == "export-save" and name and spec:
            services.storage.save_dataset(name.strip(), spec)

        saved = services.storage.list_saved_datasets()
        if not saved:
            return html.Div("Nothing saved yet.", className="op-kpi-sub")
        return html.Table(
            [
                html.Thead(
                    html.Tr([html.Th(h) for h in ("Name", "Scope", "Range", "Shape", "Saved")])
                ),
                html.Tbody(
                    [
                        html.Tr(
                            [
                                html.Td(record["name"]),
                                html.Td(
                                    record["spec"].get("port_id")
                                    or record["spec"].get("scope_mode", "—")
                                ),
                                html.Td(
                                    f"{record['spec'].get('start', '?')} → "
                                    f"{record['spec'].get('end', '?')}"
                                ),
                                html.Td(
                                    f"{record['spec'].get('mode', '?')} / "
                                    f"{record['spec'].get('interval', '?')}"
                                ),
                                html.Td(theme.format_relative(record.get("created_at"))),
                            ]
                        )
                        for record in saved
                    ]
                ),
            ],
            className="op-table",
        )
