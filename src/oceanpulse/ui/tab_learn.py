"""Tab 5 — Ocean Encyclopedia & Glossary.

Renders the content in `content.py`. Everything is searchable, because the
question a reader actually has is "what does this label on the chart mean",
and they should not have to read an essay to find out.
"""

from __future__ import annotations

from typing import Any

from dash import Input, Output, dcc, html

from . import content, theme
from .services import Services


def layout(services: Services) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Search the encyclopedia and glossary"),
                                    dcc.Input(
                                        id="learn-search",
                                        type="text",
                                        placeholder="wave power, sea level, z-score, altimetry…",
                                        className="op-input",
                                        autoComplete="off",
                                        debounce=False,
                                        style={"minWidth": "320px", "width": "100%"},
                                    ),
                                ],
                                className="op-field",
                                style={"flex": "1 1 340px"},
                            ),
                            html.Div(
                                [
                                    html.Label("View"),
                                    dcc.RadioItems(
                                        id="learn-view",
                                        options=[
                                            {"label": " Encyclopedia", "value": "encyclopedia"},
                                            {"label": " Glossary", "value": "glossary"},
                                            {"label": " Both", "value": "both"},
                                        ],
                                        value="encyclopedia",
                                        inline=True,
                                        inputStyle={"marginRight": "5px"},
                                        labelStyle={"marginRight": "16px"},
                                    ),
                                ],
                                className="op-field",
                            ),
                        ],
                        className="op-row",
                    ),
                    html.Div(
                        "Every quantity this tool can show you is explained here, "
                        "with how it is known and how far it can be trusted. "
                        "Written against the published literature as of 2026; "
                        "where the science is uncertain or this tool approximates "
                        "something, it says so.",
                        className="op-kpi-sub",
                        style={"marginTop": "10px"},
                    ),
                ],
                className="op-card",
            ),
            html.Div(className="op-spacer"),
            html.Div(id="learn-body"),
        ]
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_block(kind: str, payload: Any) -> Any:
    if kind == "h3":
        return html.H3(str(payload))
    if kind == "p":
        return html.P(str(payload))
    if kind == "ul":
        items = payload if isinstance(payload, (list, tuple)) else [payload]
        return html.Ul([html.Li(str(item)) for item in items])
    if kind == "formula":
        return html.Div(
            dcc.Markdown(str(payload), dangerously_allow_html=True),
            className="op-formula",
        )
    if kind == "note":
        return html.Div(str(payload), className="op-note", style={"margin": "12px 0"})
    if kind == "warn":
        return html.Div(
            str(payload), className="op-note warn", style={"margin": "12px 0"}
        )
    return html.P(str(payload))


def _render_section(section: content.Section) -> html.Div:
    return html.Div(
        [
            html.H2(section.title, id=f"learn-{section.key}"),
            html.Div(
                section.summary,
                className="op-card-sub",
                style={"fontSize": "13px", "marginBottom": "10px"},
            ),
            *[_render_block(kind, payload) for kind, payload in section.blocks],
        ],
        className="op-prose",
    )


def _render_term(term: content.Term) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Span(term.term),
                    html.Span(term.unit, className="op-glossary-unit") if term.unit else None,
                ],
                className="op-glossary-term",
            ),
            html.Div(term.definition, className="op-glossary-def"),
            html.Div(term.why, className="op-glossary-why") if term.why else None,
        ],
        className="op-glossary-item",
    )


def register(app: Any, services: Services) -> None:
    @app.callback(
        Output("learn-body", "children"),
        Input("learn-search", "value"),
        Input("learn-view", "value"),
    )
    def _body(query: str | None, view: str):
        sections = content.search_sections(query or "")
        terms = content.search_terms(query or "")
        show_enc = view in ("encyclopedia", "both")
        show_glo = view in ("glossary", "both")

        blocks: list[Any] = []

        if query and not sections and not terms:
            return html.Div(
                [
                    html.Div(
                        f"Nothing matches “{query}”.",
                        className="op-card-title",
                    ),
                    html.Div(
                        "Try a unit (kW/m, °C), a symbol (Hs, Tp, SLA), or a "
                        "concept (altimetry, geostrophic, heatwave).",
                        className="op-card-sub",
                    ),
                ],
                className="op-card",
            )

        if show_enc and sections:
            # Jump links, so a long page stays navigable.
            blocks.append(
                html.Div(
                    [
                        html.Div("Sections", className="op-card-title"),
                        html.Div(
                            [
                                html.A(
                                    section.title,
                                    href=f"#learn-{section.key}",
                                    className="op-chip",
                                    style={"textDecoration": "none"},
                                )
                                for section in sections
                            ],
                            className="op-toc",
                            style={"marginTop": "8px"},
                        ),
                    ],
                    className="op-card",
                )
            )
            blocks.append(html.Div(className="op-spacer"))
            for section in sections:
                blocks.append(html.Div(_render_section(section), className="op-card"))
                blocks.append(html.Div(className="op-spacer"))

        if show_glo and terms:
            blocks.append(
                html.Div(
                    [
                        html.Div(
                            f"Glossary · {len(terms)} term"
                            + ("s" if len(terms) != 1 else ""),
                            className="op-card-title",
                        ),
                        html.Div(
                            "Every symbol, unit and label used anywhere in the "
                            "interface.",
                            className="op-card-sub",
                        ),
                        html.Div([_render_term(term) for term in terms]),
                    ],
                    className="op-card",
                )
            )

        blocks.append(
            html.Div(
                [
                    html.B("Sources for this page. "),
                    "Wave statistics and spectra follow standard oceanographic "
                    "texts; the wave-power relation is the deep-water energy "
                    "flux. The marine-heatwave definition is Hobday et al. "
                    "(2016). Sea-level rise figures and the ocean heat-uptake "
                    "share follow the IPCC Sixth Assessment Report (2021–2023) "
                    "and subsequent altimetry updates. OISST methodology is "
                    "Huang et al.; the Douglas scale is a long-standing WMO "
                    "convention. Where this tool departs from a published "
                    "method — as with warm-spell detection — it is stated at the "
                    "point of use.",
                ],
                className="op-note",
                style={"marginTop": "6px"},
            )
        )
        return blocks
