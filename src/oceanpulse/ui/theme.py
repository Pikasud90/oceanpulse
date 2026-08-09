"""Colours, styling and formatting helpers.

A single dark palette, applied through an injected stylesheet rather than
per-component inline styles, so the whole interface can be re-skinned in one
place. Sequential colour scales are perceptually uniform (viridis family) -
the classic rainbow scale invents banding that is not in the data.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

# -- palette ---------------------------------------------------------------

BACKGROUND = "#071a2b"
SURFACE = "#0d2840"
SURFACE_ALT = "#123551"
BORDER = "#1d4a69"
TEXT = "#e6f2fa"
TEXT_MUTED = "#8fb3cc"
ACCENT = "#31d0c6"
ACCENT_ALT = "#4da3ff"
WARNING = "#f5a524"
DANGER = "#ff5d5d"
SUCCESS = "#3ddc84"

SST_SCALE = "Thermal"
WAVE_SCALE = "Viridis"

STATUS_COLOURS = {
    "active": SUCCESS,
    "degraded": WARNING,
    "stopped": DANGER,
}
STATUS_LABELS = {
    "active": "🟢 ACTIVE",
    "degraded": "🟡 DEGRADED",
    "stopped": "🔴 STOPPED",
}

PLOT_LAYOUT = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": TEXT, "family": "Inter, Segoe UI, system-ui, sans-serif", "size": 12},
    "margin": {"l": 56, "r": 24, "t": 42, "b": 44},
    "xaxis": {"gridcolor": "rgba(255,255,255,0.07)", "zerolinecolor": "rgba(255,255,255,0.15)"},
    "yaxis": {"gridcolor": "rgba(255,255,255,0.07)", "zerolinecolor": "rgba(255,255,255,0.15)"},
    "legend": {"bgcolor": "rgba(0,0,0,0)", "orientation": "h", "y": -0.2},
    "hoverlabel": {"bgcolor": SURFACE_ALT, "bordercolor": BORDER, "font": {"color": TEXT}},
}


def figure_layout(**overrides: Any) -> dict[str, Any]:
    layout = {key: (value.copy() if isinstance(value, dict) else value) for key, value in PLOT_LAYOUT.items()}
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(layout.get(key), dict):
            layout[key].update(value)
        else:
            layout[key] = value
    return layout


def empty_figure(message: str = "No data yet") -> dict[str, Any]:
    """A figure that explains itself instead of rendering blank axes."""
    return {
        "data": [],
        "layout": figure_layout(
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[
                {
                    "text": message,
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0.5,
                    "y": 0.5,
                    "showarrow": False,
                    "font": {"size": 14, "color": TEXT_MUTED},
                }
            ],
        ),
    }


# -- formatting ------------------------------------------------------------


def format_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def format_count(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def format_number(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:  # NaN
        return "—"
    return f"{number:,.{digits}f}{suffix}"


def format_relative(timestamp_ms: int | None) -> str:
    if not timestamp_ms:
        return "never"
    delta = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromtimestamp(
        timestamp_ms / 1000.0, tz=dt.timezone.utc
    )
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return f"in {abs(seconds) // 60}m"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_utc(timestamp_ms: int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not timestamp_ms:
        return "—"
    return dt.datetime.fromtimestamp(timestamp_ms / 1000.0, tz=dt.timezone.utc).strftime(fmt)


def compass(bearing: float | None) -> str:
    """Bearing to a 16-point compass label."""
    if bearing is None:
        return "—"
    points = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    return points[int((float(bearing) % 360.0) / 22.5 + 0.5) % 16]


# -- stylesheet ------------------------------------------------------------

STYLESHEET = f"""
:root {{
  --bg: {BACKGROUND};
  --surface: {SURFACE};
  --surface-alt: {SURFACE_ALT};
  --border: {BORDER};
  --text: {TEXT};
  --muted: {TEXT_MUTED};
  --accent: {ACCENT};
  --accent-alt: {ACCENT_ALT};
  --warning: {WARNING};
  --danger: {DANGER};
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: linear-gradient(180deg, #05131f 0%, {BACKGROUND} 45%, #061826 100%);
  background-attachment: fixed;
  color: var(--text);
  font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
  font-size: 14px;
}}
a {{ color: var(--accent); }}
.op-shell {{ max-width: 1600px; margin: 0 auto; padding: 0 20px 48px; }}

.op-header {{
  display: flex; flex-wrap: wrap; align-items: center; gap: 18px;
  padding: 16px 0 14px; border-bottom: 1px solid var(--border); margin-bottom: 18px;
}}
.op-brand {{ display: flex; align-items: baseline; gap: 10px; }}
.op-brand h1 {{ margin: 0; font-size: 22px; letter-spacing: -0.4px; }}
.op-brand span {{ color: var(--muted); font-size: 12px; }}
.op-header-controls {{ display: flex; align-items: center; gap: 14px; margin-left: auto; flex-wrap: wrap; }}
.op-status {{ display: flex; gap: 16px; align-items: center; font-size: 12px; color: var(--muted); }}
.op-status b {{ color: var(--text); font-weight: 600; }}

.op-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px 18px;
}}
.op-grid {{ display: grid; gap: 14px; }}
.op-kpis {{ grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }}
.op-kpi-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.9px; color: var(--muted); }}
.op-kpi-value {{ font-size: 26px; font-weight: 650; margin-top: 6px; letter-spacing: -0.5px; }}
.op-kpi-sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}

.op-row {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: flex-end; }}
.op-field {{ display: flex; flex-direction: column; gap: 5px; }}
.op-field label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); }}

.op-input, .op-select {{
  background: var(--surface-alt); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 8px 10px; font-size: 13px; font-family: inherit; min-width: 120px;
}}
.op-input:focus, .op-select:focus {{ outline: none; border-color: var(--accent); }}

.op-button {{
  background: var(--accent); color: #04222b; border: none; border-radius: 8px;
  padding: 9px 16px; font-weight: 620; font-size: 13px; cursor: pointer; font-family: inherit;
}}
.op-button:hover {{ filter: brightness(1.08); }}
.op-button.secondary {{ background: var(--surface-alt); color: var(--text); border: 1px solid var(--border); }}
.op-button:disabled {{ opacity: 0.45; cursor: not-allowed; }}

.op-results {{
  position: absolute; z-index: 60; top: 100%; left: 0; right: 0; margin-top: 4px;
  background: var(--surface-alt); border: 1px solid var(--border); border-radius: 10px;
  max-height: 320px; overflow-y: auto; box-shadow: 0 18px 40px rgba(0,0,0,0.5);
}}
.op-result {{ padding: 9px 12px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.05); }}
.op-result:hover {{ background: rgba(49,208,198,0.14); }}
.op-result-name {{ font-weight: 600; }}
.op-result-meta {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}

.op-ticker {{ max-height: 460px; overflow-y: auto; }}
.op-ticker-item {{
  padding: 9px 4px; border-bottom: 1px solid rgba(255,255,255,0.06);
  display: flex; justify-content: space-between; gap: 10px; font-size: 13px;
}}
.op-ticker-item b {{ color: var(--warning); }}

.op-note {{
  font-size: 12px; color: var(--muted); background: rgba(77,163,255,0.08);
  border-left: 3px solid var(--accent-alt); padding: 9px 12px; border-radius: 0 8px 8px 0;
}}
.op-note.warn {{ background: rgba(245,165,36,0.10); border-left-color: var(--warning); }}

.op-modelled {{ color: var(--warning); }}

table.op-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
table.op-table th {{
  text-align: left; padding: 8px 10px; color: var(--muted); font-weight: 600;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.7px;
  border-bottom: 1px solid var(--border);
}}
table.op-table td {{ padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
.op-scroll {{ overflow-x: auto; }}

.tab-container {{ border-bottom: 1px solid var(--border) !important; }}
.op-tab {{
  background: transparent !important; border: none !important; color: var(--muted) !important;
  padding: 11px 18px !important; font-weight: 560 !important;
}}
.op-tab--selected {{
  color: var(--text) !important; border-bottom: 2px solid var(--accent) !important;
  background: transparent !important;
}}
.op-footer {{
  margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--border);
  font-size: 11px; color: var(--muted); line-height: 1.7;
}}
"""
