"""Offline port autocomplete.

A plain text input plus an explicit result list, deliberately not a
`dcc.Dropdown`. Dash's Dropdown re-filters server-returned options on the
client by substring against the label, and gazetteer labels carry diacritics -
so typing `Bombay` retrieves Mumbai from the server and the browser then
discards it, showing "no results" for a place that was found correctly. A text
input keeps server-side ranking intact.
"""

from __future__ import annotations

from typing import Any

from dash import ALL, Input, Output, State, callback_context, dcc, html, no_update

from ..gazetteer.store import format_label
from ..logging_setup import get_logger
from .services import Services

log = get_logger(__name__)


def place_search(prefix: str, placeholder: str = "Search a port or coastal place…") -> html.Div:
    return html.Div(
        [
            html.Label("Port or coastal place"),
            html.Div(
                [
                    dcc.Input(
                        id=f"{prefix}-search-input",
                        type="text",
                        debounce=False,
                        placeholder=placeholder,
                        className="op-input",
                        autoComplete="off",
                        style={"minWidth": "320px", "width": "100%"},
                    ),
                    html.Div(id=f"{prefix}-search-results"),
                ],
                style={"position": "relative"},
            ),
            dcc.Store(id=f"{prefix}-selected-port"),
            html.Div(id=f"{prefix}-selected-label", className="op-kpi-sub"),
        ],
        className="op-field",
        style={"flex": "1 1 340px"},
    )


def register_place_search(app: Any, services: Services, prefix: str) -> None:
    """Wire the autocomplete for one instance."""

    @app.callback(
        Output(f"{prefix}-search-results", "children"),
        Input(f"{prefix}-search-input", "value"),
        prevent_initial_call=True,
    )
    def _suggest(text: str | None):
        if not text or len(text.strip()) < 2:
            return None
        if not services.gazetteer.available:
            return html.Div(
                "Port database not built — run `run.sh gazetteer`.",
                className="op-results",
                style={"padding": "10px 12px", "fontSize": "12px"},
            )
        results = services.gazetteer.search(text, limit=12)
        if not results:
            return html.Div(
                "No matching place.",
                className="op-results",
                style={"padding": "10px 12px", "fontSize": "12px"},
            )
        return html.Div(
            [
                html.Div(
                    [
                        html.Div(format_label(record), className="op-result-name"),
                        html.Div(
                            _meta_line(record),
                            className="op-result-meta",
                        ),
                    ],
                    className="op-result",
                    id={"type": f"{prefix}-result", "index": record["port_id"]},
                    n_clicks=0,
                )
                for record in results
            ],
            className="op-results",
        )

    @app.callback(
        Output(f"{prefix}-selected-port", "data"),
        Output(f"{prefix}-selected-label", "children"),
        Output(f"{prefix}-search-results", "children", allow_duplicate=True),
        Input({"type": f"{prefix}-result", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _select(clicks: list[int]):
        """Record the chosen place and close the list.

        Deliberately does *not* write the chosen label back into the search
        box. Doing so changes the input value, which re-triggers `_suggest`,
        which reopens the list the click just closed - and the obvious guard
        (compare the text against the selected record) depends on Dash having
        propagated the store before the input-change callback reads it, which
        it does not reliably do. Leaving the typed text alone removes the race
        rather than racing it, and the confirmation line below the box tells
        the user what is selected.
        """
        triggered = callback_context.triggered_id
        # Registering the pattern-matched inputs fires this once with all
        # zeros; that must not be mistaken for a selection.
        if not triggered or not any(clicks or []):
            return (no_update, no_update, no_update)
        port_id = triggered.get("index") if isinstance(triggered, dict) else None
        if not port_id:
            return (no_update, no_update, no_update)
        record = services.gazetteer.get(port_id)
        if record is None:
            return (no_update, no_update, no_update)
        detail = html.Span(
            [
                html.Span("✓ ", style={"color": "#3ddc84"}),
                html.B(format_label(record)),
                html.Span(
                    f" — {record['latitude']:.3f}, {record['longitude']:.3f}"
                    f" · {'World Port Index' if record['source'] == 'wpi' else 'GeoNames'}",
                ),
            ]
        )
        return record, detail, None


def _meta_line(record: dict[str, Any]) -> str:
    bits = [f"{record['latitude']:.2f}, {record['longitude']:.2f}"]
    if record.get("source") == "wpi":
        bits.append("port")
        if record.get("harbor_size"):
            bits.append(f"harbour size {record['harbor_size']}")
    else:
        bits.append("coastal place")
        if record.get("population"):
            bits.append(f"pop. {int(record['population']):,}")
    return " · ".join(bits)
