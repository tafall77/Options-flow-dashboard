"""Dark trading-terminal theme shared by every chart and HTML panel.

Colors come from a CVD-validated palette (dark mode, surface #1a1a19):
calls/positive = blue, puts/negative = red (a diverging pair), extra series
take the categorical order blue, orange, aqua, yellow.
"""

import plotly.graph_objects as go
import plotly.io as pio

SURFACE = "#1a1a19"
PAGE = "#0d0d0d"
INK = "#ffffff"
INK_2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"
BORDER = "rgba(255,255,255,0.10)"

CALL = "#3987e5"   # calls, positive exposure, bullish
PUT = "#e66767"    # puts, negative exposure, bearish
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500"]   # categorical order
LEVEL = "#c98500"  # gamma-flip marker (categorical yellow, not a status color)
GOOD = "#0ca30c"
WARN = "#fab219"
CRIT = "#d03b3b"

# Sequential blue ramp (light -> dark) for the IV surface.
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

TEMPLATE = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, color=INK_2, size=12),
        title=dict(font=dict(color=INK, size=14), x=0.01, xanchor="left", y=0.975, yanchor="top"),
        colorway=SERIES,
        margin=dict(l=56, r=20, t=84, b=44),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#262624", bordercolor=AXIS, font=dict(color=INK, family=FONT)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(color=INK_2, size=11)),
        xaxis=dict(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickcolor=AXIS,
                   tickfont=dict(color=MUTED), title=dict(font=dict(color=MUTED, size=11))),
        yaxis=dict(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickcolor=AXIS,
                   tickfont=dict(color=MUTED), title=dict(font=dict(color=MUTED, size=11))),
        bargap=0.15,
    )
)
pio.templates["optionsdash"] = TEMPLATE

CSS = f"""
<style>
.od-root {{ font-family: {FONT}; color: {INK_2}; }}
.od-kpis {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; }}
.od-kpi {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 12px; }}
.od-kpi .l {{ font-size: 11px; color: {MUTED}; text-transform: uppercase; letter-spacing: .04em; }}
.od-kpi .v {{ font-size: 22px; color: {INK}; margin-top: 2px; }}
.od-kpi .s {{ font-size: 12px; color: {INK_2}; margin-top: 2px; }}
.od-up {{ color: {GOOD}; }} .od-dn {{ color: {PUT}; }}
.od-panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 14px; }}
.od-panel h4 {{ margin: 0 0 6px 0; color: {INK}; font-size: 14px; font-weight: 600; }}
.od-panel ul {{ margin: 0; padding-left: 18px; }} .od-panel li {{ margin: 3px 0; line-height: 1.35; }}
.od-tag {{ display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 4px;
          border: 1px solid {BORDER}; color: {INK}; margin-right: 6px; }}
.od-table {{ border-collapse: collapse; width: 100%; font-size: 12px; font-variant-numeric: tabular-nums; }}
.od-table th {{ text-align: right; color: {MUTED}; font-weight: 500; padding: 4px 6px; border-bottom: 1px solid {AXIS}; }}
.od-table td {{ text-align: right; padding: 3px 6px; border-bottom: 1px solid {GRID}; color: {INK_2}; }}
.od-table th:first-child, .od-table td:first-child {{ text-align: left; }}
.od-bar {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.od-title {{ font-size: 18px; color: {INK}; font-weight: 600; }}
.od-status {{ font-size: 12px; color: {MUTED}; }}
.od-box {{ background: {PAGE}; padding: 12px; border-radius: 10px; }}
.od-box .widget-label, .od-box label {{ color: {INK_2}; }}
.od-box .lm-TabBar-tab {{ background: {SURFACE} !important; color: {INK_2} !important; border-color: {AXIS} !important; }}
.od-box .lm-TabBar-tab.lm-mod-current {{ background: {AXIS} !important; color: {INK} !important; }}
.od-box .widget-tab-contents, .od-box .widget-tab > .widget-tab-contents {{ background: {PAGE}; border-color: {AXIS}; }}
.od-live {{ color: {GOOD}; }} .od-demo {{ color: {WARN}; }} .od-err {{ color: {PUT}; }}
</style>
"""
