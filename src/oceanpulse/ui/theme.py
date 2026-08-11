"""Design system: palette, type scale, component styles, formatting helpers.

One stylesheet, injected once, so the whole interface can be re-skinned from
here. Two rules shape the choices:

**Contrast is not decoration.** Body text sits at roughly 12:1 against the
background and the muted tone at about 6:1, both comfortably past WCAG AA.
Colour is never the only carrier of meaning - status also has a glyph, severity
also has a label, and the map legend also has numbers.

**Sequential scales are perceptually uniform.** Viridis-family and Thermal
ramps step evenly in lightness, so equal steps in the data look like equal
steps on screen. A rainbow ramp invents banding that is not in the data.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

BACKGROUND = "#04121f"
BACKGROUND_ALT = "#061a2b"
SURFACE = "#0c2438"
SURFACE_ALT = "#123249"
SURFACE_HIGH = "#18415c"
BORDER = "#1d4a69"
BORDER_SOFT = "rgba(255,255,255,0.08)"

TEXT = "#eaf4fb"
TEXT_MUTED = "#93b6cf"
TEXT_FAINT = "#6b90aa"

ACCENT = "#2fd4c4"
ACCENT_DEEP = "#12a99b"
ACCENT_ALT = "#5aa9ff"
WARNING = "#f5a524"
DANGER = "#ff5f6d"
SUCCESS = "#3ddc84"
VIOLET = "#a78bfa"

# Sequential scales, chosen for perceptual uniformity.
SST_SCALE = "Thermal"
WAVE_SCALE = "Viridis"
POWER_SCALE = "Plasma"
CURRENT_SCALE = "Ice"

# Categorical series colours, ordered for maximum separation.
SERIES = (ACCENT, WARNING, ACCENT_ALT, VIOLET, SUCCESS, DANGER)

STATUS_COLOURS = {"active": SUCCESS, "degraded": WARNING, "stopped": DANGER}
STATUS_LABELS = {
    "active": "🟢 ACTIVE",
    "degraded": "🟡 DEGRADED",
    "stopped": "🔴 STOPPED",
}

# Sea-state severity bands, after the Douglas scale. Each has a word as well as
# a colour so the meaning survives a greyscale print or colour-blind viewer.
SEA_STATE_BANDS = (
    (0.1, "Calm", TEXT_FAINT),
    (0.5, "Smooth", "#5ec8e8"),
    (1.25, "Slight", "#54b0e0"),
    (2.5, "Moderate", SUCCESS),
    (4.0, "Rough", WARNING),
    (6.0, "Very rough", "#ff8c42"),
    (9.0, "High", DANGER),
    (14.0, "Very high", "#e0417a"),
    (999.0, "Phenomenal", VIOLET),
)


def sea_state(wave_height_m: float | None) -> tuple[str, str]:
    """Douglas sea-state word and colour for a significant wave height."""
    if wave_height_m is None:
        return ("Unknown", TEXT_FAINT)
    try:
        height = float(wave_height_m)
    except (TypeError, ValueError):
        return ("Unknown", TEXT_FAINT)
    for ceiling, label, colour in SEA_STATE_BANDS:
        if height < ceiling:
            return (label, colour)
    return ("Phenomenal", VIOLET)


# --------------------------------------------------------------------------
# Plotly layout
# --------------------------------------------------------------------------

FONT_STACK = "Inter, 'Segoe UI', system-ui, -apple-system, sans-serif"

PLOT_LAYOUT: dict[str, Any] = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": TEXT, "family": FONT_STACK, "size": 12},
    "margin": {"l": 66, "r": 26, "t": 56, "b": 54},
    "xaxis": {
        "gridcolor": "rgba(255,255,255,0.06)",
        "zerolinecolor": "rgba(255,255,255,0.16)",
        "linecolor": "rgba(255,255,255,0.18)",
        "title": {"font": {"size": 12, "color": TEXT_MUTED}},
    },
    "yaxis": {
        "gridcolor": "rgba(255,255,255,0.06)",
        "zerolinecolor": "rgba(255,255,255,0.16)",
        "linecolor": "rgba(255,255,255,0.18)",
        "title": {"font": {"size": 12, "color": TEXT_MUTED}},
    },
    "legend": {
        "bgcolor": "rgba(10,32,50,0.75)",
        "bordercolor": BORDER,
        "borderwidth": 1,
        "orientation": "h",
        "yanchor": "bottom",
        "y": 1.02,
        "x": 0,
        "font": {"size": 11},
    },
    "hoverlabel": {
        "bgcolor": SURFACE_ALT,
        "bordercolor": ACCENT,
        "font": {"color": TEXT, "family": FONT_STACK, "size": 12},
    },
}


def figure_layout(**overrides: Any) -> dict[str, Any]:
    layout = {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in PLOT_LAYOUT.items()
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(layout.get(key), dict):
            merged = layout[key].copy()
            merged.update(value)
            layout[key] = merged
        else:
            layout[key] = value
    return layout


def chart_title(text: str, subtitle: str = "") -> dict[str, Any]:
    """Title with an optional explanatory second line.

    Every chart in the interface says what it shows and in what units; a bare
    plot leaves the reader guessing at exactly the moment they need to trust it.
    """
    if subtitle:
        full = f"{text}<br><span style='font-size:11px;color:{TEXT_MUTED}'>{subtitle}</span>"
    else:
        full = text
    return {"text": full, "x": 0.0, "xanchor": "left", "font": {"size": 14}}


def empty_figure(message: str = "No data yet", hint: str = "") -> dict[str, Any]:
    """A figure that explains itself instead of rendering blank axes."""
    text = f"<b>{message}</b>"
    if hint:
        text += f"<br><span style='font-size:11px;color:{TEXT_MUTED}'>{hint}</span>"
    return {
        "data": [],
        "layout": figure_layout(
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[
                {
                    "text": text,
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0.5,
                    "y": 0.5,
                    "showarrow": False,
                    "align": "center",
                    "font": {"size": 13, "color": TEXT_MUTED},
                }
            ],
        ),
    }


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


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


COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def compass(bearing: float | None) -> str:
    """Bearing to a 16-point compass label."""
    if bearing is None:
        return "—"
    try:
        value = float(bearing)
    except (TypeError, ValueError):
        return "—"
    return COMPASS_POINTS[int((value % 360.0) / 22.5 + 0.5) % 16]


def bearing_text(bearing: float | None) -> str:
    """`WSW 247°` - the word and the number, because each answers a different question."""
    if bearing is None:
        return "—"
    try:
        value = float(bearing) % 360.0
    except (TypeError, ValueError):
        return "—"
    return f"{compass(value)} {value:.0f}°"


def format_latlon(lat: float | None, lon: float | None) -> str:
    """Signed decimals are ambiguous to read; hemisphere letters are not."""
    if lat is None or lon is None:
        return "—"
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return "—"
    ns = "N" if lat_f >= 0 else "S"
    ew = "E" if lon_f >= 0 else "W"
    return f"{abs(lat_f):.2f}°{ns}, {abs(lon_f):.2f}°{ew}"


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle initial bearing from point 1 to point 2, in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------

STYLESHEET = f"""
:root {{
  --bg: {BACKGROUND};
  --bg-alt: {BACKGROUND_ALT};
  --surface: {SURFACE};
  --surface-alt: {SURFACE_ALT};
  --surface-high: {SURFACE_HIGH};
  --border: {BORDER};
  --border-soft: {BORDER_SOFT};
  --text: {TEXT};
  --muted: {TEXT_MUTED};
  --faint: {TEXT_FAINT};
  --accent: {ACCENT};
  --accent-deep: {ACCENT_DEEP};
  --accent-alt: {ACCENT_ALT};
  --warning: {WARNING};
  --danger: {DANGER};
  --success: {SUCCESS};
  --radius: 12px;
  --radius-sm: 8px;
  --shadow: 0 10px 30px rgba(0,0,0,0.35);
}}

* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}

body {{
  margin: 0;
  background:
    radial-gradient(1200px 600px at 20% -10%, rgba(47,212,196,0.10), transparent 60%),
    radial-gradient(1000px 500px at 90% 0%, rgba(90,169,255,0.08), transparent 55%),
    linear-gradient(180deg, #03101b 0%, var(--bg) 40%, var(--bg-alt) 100%);
  background-attachment: fixed;
  color: var(--text);
  font-family: {FONT_STACK};
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}}

a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* Keyboard users must be able to see where they are. */
:focus-visible {{
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 4px;
}}

::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-track {{ background: rgba(0,0,0,0.2); }}
::-webkit-scrollbar-thumb {{ background: var(--surface-high); border-radius: 6px; }}
::-webkit-scrollbar-thumb:hover {{ background: #22587c; }}

.op-shell {{ max-width: 1680px; margin: 0 auto; padding: 0 22px 56px; }}

/* ---------- header ---------- */

.op-header {{
  display: flex; flex-wrap: wrap; align-items: center; gap: 20px;
  padding: 16px 0 14px; margin-bottom: 16px;
  border-bottom: 1px solid var(--border);
}}
.op-brand {{
  display: flex; align-items: baseline; gap: 11px;
  background: none; border: none; padding: 6px 8px; margin: -6px -8px;
  cursor: pointer; font-family: inherit; text-align: left;
  border-radius: var(--radius-sm); transition: background 120ms ease;
}}
.op-brand:hover {{ background: rgba(47,212,196,0.10); }}
.op-brand h1 {{
  margin: 0; font-size: 23px; font-weight: 700; letter-spacing: -0.5px;
  background: linear-gradient(92deg, var(--accent) 0%, var(--accent-alt) 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}}
.op-brand span {{ color: var(--muted); font-size: 12px; }}
.op-header-controls {{
  display: flex; align-items: center; gap: 16px; margin-left: auto; flex-wrap: wrap;
}}
.op-status {{
  display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  font-size: 12px; color: var(--muted);
}}
.op-status b {{ color: var(--text); font-weight: 650; }}
.op-status-item {{
  display: flex; flex-direction: column; line-height: 1.3;
  padding-left: 14px; border-left: 1px solid var(--border-soft);
}}
.op-status-item:first-child {{ padding-left: 0; border-left: none; }}
.op-status-label {{
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.9px; color: var(--faint);
}}

/* ---------- cards ---------- */

.op-card {{
  background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0) 45%),
              var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  box-shadow: var(--shadow);
}}
.op-card--flush {{ padding: 0; overflow: hidden; }}
.op-card-title {{
  font-weight: 650; font-size: 14px; margin-bottom: 2px;
  display: flex; align-items: center; gap: 8px;
}}
.op-card-sub {{ font-size: 12px; color: var(--muted); margin-bottom: 12px; }}

.op-grid {{ display: grid; gap: 14px; }}
.op-kpis {{ grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); }}
.op-kpi-label {{
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted);
}}
.op-kpi-value {{
  font-size: 27px; font-weight: 680; margin-top: 6px; letter-spacing: -0.6px;
  font-variant-numeric: tabular-nums;
}}
.op-kpi-sub {{ font-size: 11.5px; color: var(--muted); margin-top: 4px; }}

/* ---------- controls ---------- */

.op-row {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: flex-end; }}
.op-field {{ display: flex; flex-direction: column; gap: 6px; }}
.op-field label {{
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.9px; color: var(--muted);
}}

.op-input, .op-select {{
  background: var(--surface-alt); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--radius-sm); padding: 9px 11px; font-size: 13px;
  font-family: inherit; min-width: 120px; transition: border-color 120ms ease;
}}
.op-input:hover, .op-select:hover {{ border-color: var(--surface-high); }}
.op-input:focus, .op-select:focus {{ outline: none; border-color: var(--accent); }}
.op-input::placeholder {{ color: var(--faint); }}

.op-button {{
  background: linear-gradient(180deg, var(--accent) 0%, var(--accent-deep) 100%);
  color: #03222b; border: none; border-radius: var(--radius-sm);
  padding: 10px 17px; font-weight: 660; font-size: 13px; cursor: pointer;
  font-family: inherit; transition: filter 120ms ease, transform 120ms ease;
}}
.op-button:hover {{ filter: brightness(1.1); }}
.op-button:active {{ transform: translateY(1px); }}
.op-button.secondary {{
  background: var(--surface-alt); color: var(--text); border: 1px solid var(--border);
}}
.op-button.secondary:hover {{ background: var(--surface-high); filter: none; }}
.op-button:disabled {{ opacity: 0.45; cursor: not-allowed; }}
.op-button--sm {{ padding: 6px 11px; font-size: 12px; }}

/* Dash injects its own dropdown markup; these reach inside it. */
.Select-control, .Select-menu-outer {{
  background-color: var(--surface-alt) !important;
  border-color: var(--border) !important;
  color: var(--text) !important;
}}
.Select-value-label, .Select-placeholder, .Select-option {{ color: var(--text) !important; }}
.Select-option {{ background-color: var(--surface-alt) !important; }}
.Select-option.is-focused {{ background-color: var(--surface-high) !important; }}
.DateInput_input, .DateRangePickerInput {{
  background: var(--surface-alt) !important; color: var(--text) !important;
  border-color: var(--border) !important;
}}
.DateInput_input__focused {{ border-bottom-color: var(--accent) !important; }}

/* ---------- search ---------- */

.op-results {{
  position: absolute; z-index: 60; top: 100%; left: 0; right: 0; margin-top: 5px;
  background: var(--surface-alt); border: 1px solid var(--border);
  border-radius: var(--radius); max-height: 340px; overflow-y: auto;
  box-shadow: 0 20px 44px rgba(0,0,0,0.55);
}}
.op-result {{
  padding: 10px 13px; cursor: pointer; border-bottom: 1px solid var(--border-soft);
}}
.op-result:last-child {{ border-bottom: none; }}
.op-result:hover {{ background: rgba(47,212,196,0.14); }}
.op-result-name {{ font-weight: 620; }}
.op-result-meta {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}

/* ---------- data list / ticker ---------- */

.op-ticker {{ max-height: 470px; overflow-y: auto; margin: 0 -4px; }}
.op-listrow {{
  border-bottom: 1px solid var(--border-soft); padding: 9px 6px;
  border-radius: var(--radius-sm); transition: background 120ms ease;
}}
.op-listrow:hover {{ background: rgba(255,255,255,0.035); }}
.op-listrow-head {{
  display: flex; align-items: baseline; gap: 10px; cursor: pointer;
  background: none; border: none; color: inherit; font: inherit;
  width: 100%; text-align: left; padding: 0;
}}
.op-listrow-metric {{
  font-size: 16px; font-weight: 680; font-variant-numeric: tabular-nums; min-width: 62px;
}}
.op-listrow-where {{ flex: 1; min-width: 0; }}
.op-listrow-place {{
  font-size: 12.5px; font-weight: 600; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}}
.op-listrow-coords {{ font-size: 10.5px; color: var(--faint); font-variant-numeric: tabular-nums; }}
.op-listrow-chev {{ color: var(--faint); font-size: 11px; }}
.op-badge {{
  font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.7px;
  padding: 2px 7px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap;
}}

.op-detail {{
  margin-top: 9px; padding: 11px 12px; background: rgba(0,0,0,0.24);
  border: 1px solid var(--border-soft); border-radius: var(--radius-sm);
}}
.op-detail-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 10px 16px;
}}
.op-detail-key {{
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--faint);
}}
.op-detail-val {{ font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }}

/* ---------- notes ---------- */

.op-note {{
  font-size: 12px; color: var(--muted); background: rgba(90,169,255,0.09);
  border-left: 3px solid var(--accent-alt); padding: 10px 13px;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}}
.op-note.warn {{ background: rgba(245,165,36,0.11); border-left-color: var(--warning); }}
.op-note.danger {{ background: rgba(255,95,109,0.11); border-left-color: var(--danger); }}
.op-note b {{ color: var(--text); }}
.op-modelled {{ color: var(--warning); }}

/* ---------- tables ---------- */

table.op-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
table.op-table th {{
  text-align: left; padding: 9px 11px; color: var(--muted); font-weight: 650;
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.8px;
  border-bottom: 1px solid var(--border); position: sticky; top: 0;
  background: var(--surface); z-index: 2; white-space: nowrap;
}}
table.op-table td {{
  padding: 8px 11px; border-bottom: 1px solid var(--border-soft);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}}
table.op-table tr:hover td {{ background: rgba(255,255,255,0.03); }}
.op-scroll {{ overflow-x: auto; max-height: 460px; overflow-y: auto; }}

/* ---------- tabs ---------- */

.tab-container {{ border-bottom: 1px solid var(--border) !important; }}
.op-tab {{
  background: transparent !important; border: none !important; color: var(--muted) !important;
  padding: 12px 19px !important; font-weight: 570 !important; font-size: 13.5px !important;
  transition: color 120ms ease !important;
}}
.op-tab:hover {{ color: var(--text) !important; }}
.op-tab--selected {{
  color: var(--text) !important; background: transparent !important;
  border-bottom: 2px solid var(--accent) !important; font-weight: 660 !important;
}}

/* ---------- compass ---------- */

.op-compass-wrap {{
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
}}
.op-compass {{
  position: relative; width: 108px; height: 108px; flex: 0 0 auto;
  border-radius: 50%; border: 1px solid var(--border);
  background: radial-gradient(circle at 50% 42%, rgba(47,212,196,0.10), rgba(0,0,0,0.30) 70%);
}}
.op-compass-label {{
  position: absolute; font-size: 9.5px; font-weight: 700; color: var(--faint);
  letter-spacing: 0.5px;
}}
.op-compass-label.n {{ top: 4px; left: 50%; transform: translateX(-50%); color: var(--danger); }}
.op-compass-label.s {{ bottom: 4px; left: 50%; transform: translateX(-50%); }}
.op-compass-label.e {{ right: 5px; top: 50%; transform: translateY(-50%); }}
.op-compass-label.w {{ left: 5px; top: 50%; transform: translateY(-50%); }}
.op-compass-needle {{
  position: absolute; left: 50%; top: 50%; width: 2.5px; height: 40px;
  border-radius: 2px; transform-origin: 50% 100%; background: var(--accent);
  box-shadow: 0 0 9px rgba(47,212,196,0.65);
}}
.op-compass-hub {{
  position: absolute; left: 50%; top: 50%; width: 8px; height: 8px; margin: -4px 0 0 -4px;
  border-radius: 50%; background: var(--text);
}}
.op-compass-read {{ font-variant-numeric: tabular-nums; }}
.op-compass-read .big {{ font-size: 22px; font-weight: 690; letter-spacing: -0.4px; }}

/* ---------- encyclopedia ---------- */

.op-prose {{ max-width: 74ch; font-size: 14px; line-height: 1.72; }}
.op-prose h2 {{
  font-size: 19px; margin: 30px 0 8px; letter-spacing: -0.3px;
  padding-bottom: 6px; border-bottom: 1px solid var(--border-soft);
}}
.op-prose h3 {{ font-size: 15px; margin: 22px 0 6px; color: var(--accent); }}
.op-prose p {{ margin: 9px 0; color: #dcebf5; }}
.op-prose ul {{ margin: 9px 0; padding-left: 22px; color: #dcebf5; }}
.op-prose li {{ margin: 5px 0; }}
.op-prose code {{
  background: rgba(0,0,0,0.35); padding: 1px 6px; border-radius: 4px;
  font-size: 12.5px; color: var(--accent);
}}
.op-formula {{
  background: rgba(0,0,0,0.32); border: 1px solid var(--border-soft);
  border-radius: var(--radius-sm); padding: 12px 14px; margin: 12px 0;
  font-size: 14px; text-align: center; color: var(--text);
}}
.op-toc {{
  display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 6px;
}}
.op-chip {{
  background: var(--surface-alt); border: 1px solid var(--border); color: var(--muted);
  border-radius: 999px; padding: 6px 13px; font-size: 12px; cursor: pointer;
  font-family: inherit; transition: all 120ms ease;
}}
.op-chip:hover {{ color: var(--text); border-color: var(--surface-high); }}
.op-chip--on {{
  background: rgba(47,212,196,0.16); border-color: var(--accent); color: var(--text);
  font-weight: 620;
}}

.op-glossary-item {{
  padding: 12px 0; border-bottom: 1px solid var(--border-soft);
}}
.op-glossary-term {{
  font-weight: 660; font-size: 14px; display: flex; align-items: baseline;
  gap: 9px; flex-wrap: wrap;
}}
.op-glossary-unit {{
  font-size: 11px; color: var(--accent); background: rgba(47,212,196,0.12);
  padding: 1px 8px; border-radius: 999px; font-weight: 600;
}}
.op-glossary-def {{ font-size: 13px; color: #d6e7f2; margin-top: 4px; }}
.op-glossary-why {{ font-size: 12px; color: var(--muted); margin-top: 4px; font-style: italic; }}

/* ---------- misc ---------- */

.op-split {{ display: flex; gap: 14px; flex-wrap: wrap; }}
.op-spacer {{ height: 14px; }}
.op-footer {{
  margin-top: 34px; padding-top: 18px; border-top: 1px solid var(--border);
  font-size: 11.5px; color: var(--muted); line-height: 1.75;
}}
.op-budget {{ display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px; }}
.op-budget-bar {{
  height: 4px; border-radius: 2px; background: rgba(255,255,255,0.10);
  overflow: hidden; margin-top: 3px; min-width: 74px;
}}
.op-budget-fill {{ height: 100%; background: var(--accent); border-radius: 2px; }}

@media (max-width: 860px) {{
  .op-shell {{ padding: 0 14px 40px; }}
  .op-header {{ gap: 12px; }}
  .op-header-controls {{ margin-left: 0; width: 100%; }}
  .op-kpi-value {{ font-size: 23px; }}
  .op-prose {{ max-width: none; }}
}}
"""
