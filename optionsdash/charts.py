"""Plotly figure builders. Each takes a Snapshot and returns a go.Figure.

They are pure functions, so the same figures serve the live widget dashboard,
static notebook output and the HTML export.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import theme as T
from .analytics import Snapshot, _money

HEIGHT = 360


def _scale(values) -> tuple[float, str]:
    v = np.nanmax(np.abs(np.asarray(values, dtype=float))) if len(values) else 0
    if not np.isfinite(v) or v == 0:
        return 1.0, "$"
    if v >= 1e9:
        return 1e9, "$B"
    if v >= 1e6:
        return 1e6, "$M"
    if v >= 1e3:
        return 1e3, "$K"
    return 1.0, "$"


def _base(title: str, s: Snapshot, **layout) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="optionsdash", title=title, height=HEIGHT,
                      uirevision=s.symbol, **layout)
    return fig


def _empty(fig: go.Figure, msg: str = "No data yet") -> go.Figure:
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                       font=dict(color=T.MUTED, size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def _vline(fig, x, label, color=T.INK_2, pos="top"):
    if x is None or not np.isfinite(x):
        return
    fig.add_vline(x=x, line=dict(color=color, width=1), layer="above")
    fig.add_annotation(x=x, y=1 if pos == "top" else 0.02, xref="x", yref="paper", text=label,
                       showarrow=False, xanchor="left", yanchor="top" if pos == "top" else "bottom",
                       font=dict(color=color, size=11), bgcolor=T.SURFACE)


def _hline(fig, y, label, color=T.INK_2):
    if y is None or not np.isfinite(y):
        return
    fig.add_hline(y=y, line=dict(color=color, width=1), layer="above")
    fig.add_annotation(x=1, y=y, xref="paper", yref="y", text=label, showarrow=False,
                       xanchor="right", yanchor="bottom", font=dict(color=color, size=11),
                       bgcolor=T.SURFACE)


def _strike_window(df, spot, band):
    return df[(df["strike"] / spot - 1).abs() <= band]


# ----------------------------------------------------------------------------
# Price & levels
# ----------------------------------------------------------------------------

def price_levels(s: Snapshot) -> go.Figure:
    fig = _base(f"{s.symbol} intraday with dealer levels", s, hovermode="x")
    bars = s.intraday
    if bars.empty:
        return _empty(fig)
    ts = pd.to_datetime(bars["ts"]).dt.tz_convert("America/New_York").dt.tz_localize(None)
    m = s.metrics
    em = m.get("em_front")
    if em and np.isfinite(em):
        fig.add_hrect(y0=s.spot - em, y1=s.spot + em, fillcolor=T.CALL, opacity=0.08, line_width=0)
        fig.add_annotation(x=0, y=s.spot + em, xref="paper", yref="y", text=f"expected move ±{em:.2f}",
                           showarrow=False, xanchor="left", yanchor="bottom",
                           font=dict(color=T.MUTED, size=11))
    fig.add_trace(go.Scatter(x=ts, y=bars["close"], mode="lines", name=s.symbol,
                             line=dict(color=T.INK, width=2),
                             hovertemplate="%{x|%H:%M}  %{y:.2f}<extra></extra>"))
    _hline(fig, m.get("call_wall"), f"call wall {m.get('call_wall', 0):.0f}", T.CALL)
    _hline(fig, m.get("put_wall"), f"put wall {m.get('put_wall', 0):.0f}", T.PUT)
    if s.flip:
        _hline(fig, s.flip, f"gamma flip {s.flip:.1f}", T.LEVEL)
    if m.get("max_pain"):
        _hline(fig, m["max_pain"], f"max pain {m['max_pain']:.0f}", T.MUTED)
    lo = np.nanmin([bars["low"].min(), s.spot - (em or 0)])
    hi = np.nanmax([bars["high"].max(), s.spot + (em or 0)])
    levels = [v for v in (m.get("call_wall"), m.get("put_wall"), s.flip, m.get("max_pain"))
              if v and np.isfinite(v) and abs(v / s.spot - 1) < 0.03]
    lo, hi = min([lo] + levels), max([hi] + levels)
    pad = (hi - lo) * 0.08
    fig.update_yaxes(range=[lo - pad, hi + pad], title="price")
    fig.update_layout(showlegend=False)
    return fig


# ----------------------------------------------------------------------------
# Dealer positioning
# ----------------------------------------------------------------------------

def gex_by_strike(s: Snapshot, band: float = 0.05) -> go.Figure:
    fig = _base("Dealer gamma exposure by strike", s, barmode="relative")
    e = _strike_window(s.exposure, s.spot, band)
    if e.empty:
        return _empty(fig)
    k, unit = _scale(np.r_[e["call_gex"], e["put_gex"]])
    fig.add_trace(go.Bar(x=e["strike"], y=e["call_gex"] / k, name="calls", marker_color=T.CALL,
                         hovertemplate="strike %{x}<br>call GEX %{y:.2f}" + unit + "<extra></extra>"))
    fig.add_trace(go.Bar(x=e["strike"], y=e["put_gex"] / k, name="puts", marker_color=T.PUT,
                         hovertemplate="strike %{x}<br>put GEX %{y:.2f}" + unit + "<extra></extra>"))
    _vline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    if s.flip:
        _vline(fig, s.flip, f"flip {s.flip:.1f}", T.LEVEL, pos="bottom")
    fig.update_yaxes(title=f"{unit} per 1% move")
    fig.update_xaxes(title=f"strike  (weight: {s.metrics['weight_label']})")
    fig.update_layout(hovermode="closest", bargap=0.1)
    return fig


def gamma_profile(s: Snapshot) -> go.Figure:
    fig = _base("Net dealer gamma vs spot (re-priced)", s, hovermode="x")
    p = s.profile
    if p.empty:
        return _empty(fig)
    k, unit = _scale(p["gex"])
    y = p["gex"] / k
    fig.add_trace(go.Scatter(x=p["spot"], y=y.clip(lower=0), mode="lines", name="positive gamma",
                             line=dict(color=T.CALL, width=2), fill="tozeroy",
                             fillcolor="rgba(57,135,229,0.15)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=p["spot"], y=y.clip(upper=0), mode="lines", name="negative gamma",
                             line=dict(color=T.PUT, width=2), fill="tozeroy",
                             fillcolor="rgba(230,103,103,0.15)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=p["spot"], y=y, mode="lines", line=dict(width=0), showlegend=False,
                             hovertemplate="if spot = %{x:.2f}<br>net GEX %{y:.2f}" + unit + "<extra></extra>"))
    _vline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    if s.flip:
        _vline(fig, s.flip, f"flip {s.flip:.1f}", T.LEVEL, pos="bottom")
    fig.update_yaxes(title=f"{unit} per 1% move", zeroline=True, zerolinecolor=T.MUTED)
    fig.update_xaxes(title="hypothetical spot")
    return fig


def _signed_bars(fig, x, y, unit, label):
    colors = np.where(y >= 0, T.CALL, T.PUT)
    fig.add_trace(go.Bar(x=x, y=y, marker_color=colors, showlegend=False,
                         hovertemplate="strike %{x}<br>" + label + " %{y:.2f}" + unit + "<extra></extra>"))


def vanna_by_strike(s: Snapshot, band: float = 0.06) -> go.Figure:
    fig = _base("Dealer vanna: delta change per +1 vol pt", s)
    e = _strike_window(s.exposure, s.spot, band)
    if e.empty:
        return _empty(fig)
    k, unit = _scale(e["vanna"])
    _signed_bars(fig, e["strike"], e["vanna"] / k, unit, "vanna")
    _vline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    fig.update_yaxes(title=f"{unit} delta per vol pt")
    fig.update_xaxes(title="strike  (+ : dealers sell if IV rises / buy if IV falls)")
    fig.update_layout(hovermode="closest")
    return fig


def charm_by_strike(s: Snapshot, band: float = 0.06) -> go.Figure:
    fig = _base("Dealer charm: delta decay per day", s)
    e = _strike_window(s.exposure, s.spot, band)
    if e.empty:
        return _empty(fig)
    k, unit = _scale(e["charm"])
    _signed_bars(fig, e["strike"], e["charm"] / k, unit, "charm")
    _vline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    fig.update_yaxes(title=f"{unit} delta per day")
    fig.update_xaxes(title="strike  (+ : dealer hedges sell as time passes, - : buy)")
    fig.update_layout(hovermode="closest")
    return fig


def positioning(s: Snapshot, band: float = 0.06) -> go.Figure:
    label = s.metrics["weight_label"]
    fig = _base(f"Positioning by strike ({label.split(' (')[0]})", s, barmode="relative")
    e = _strike_window(s.exposure, s.spot, band)
    if e.empty:
        return _empty(fig)
    fig.add_trace(go.Bar(x=e["strike"], y=e["call_w"], name="calls", marker_color=T.CALL,
                         hovertemplate="strike %{x}<br>calls %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(x=e["strike"], y=-e["put_w"], name="puts", marker_color=T.PUT,
                         customdata=e["put_w"],
                         hovertemplate="strike %{x}<br>puts %{customdata:,.0f}<extra></extra>"))
    _vline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    mp = s.metrics.get("max_pain")
    if mp:
        _vline(fig, mp, f"max pain {mp:.0f}", T.MUTED, pos="bottom")
    fig.update_yaxes(title="contracts (puts shown below zero)")
    fig.update_xaxes(title="strike")
    fig.update_layout(hovermode="closest", bargap=0.1)
    return fig


# ----------------------------------------------------------------------------
# Volatility
# ----------------------------------------------------------------------------

def term_structure(s: Snapshot) -> go.Figure:
    fig = _base("ATM implied vol term structure", s, hovermode="x")
    t = s.term.dropna(subset=["atm_iv"])
    if t.empty:
        return _empty(fig)
    fig.add_trace(go.Scatter(x=t["dte"], y=t["atm_iv"] * 100, mode="lines+markers", name="ATM IV",
                             line=dict(color=T.CALL, width=2), marker=dict(size=8),
                             customdata=t["expiry"].dt.strftime("%b %d"),
                             hovertemplate="%{customdata} (%{x:.1f}d)<br>ATM IV %{y:.2f}<extra></extra>"))
    rv = s.metrics.get("rv20")
    if rv and np.isfinite(rv):
        _hline(fig, rv * 100, f"20d realized {rv * 100:.1f}", T.MUTED)
    fig.update_yaxes(title="implied vol (%)")
    fig.update_xaxes(title="days to expiry")
    fig.update_layout(showlegend=False)
    return fig


def _pick_expiries(term: pd.DataFrame, targets=(0, 7, 30, 60)):
    if term.empty:
        return []
    picked = []
    for t in targets:
        i = (term["dte"] - t).abs().idxmin()
        e = term.loc[i, "expiry"]
        if e not in picked:
            picked.append(e)
    return picked


def smile(s: Snapshot) -> go.Figure:
    fig = _base("Volatility smile (OTM options)", s, hovermode="closest")
    sm = s.smile
    exps = _pick_expiries(s.term)
    if sm.empty or not exps:
        return _empty(fig)
    atm = s.term.set_index("expiry")["atm_iv"]
    for i, e in enumerate(exps):
        g = sm[sm["expiry"] == e]
        if g.empty:
            continue
        width = max(0.015, min(0.10, 3 * atm.get(e, 0.2) * np.sqrt(g["T"].iloc[0])))
        g = g[g["moneyness"].abs() <= width]
        if g.empty:
            continue
        name = f"{pd.Timestamp(e):%b %d} ({g['dte'].iloc[0]:.0f}d)"
        fig.add_trace(go.Scatter(x=g["moneyness"] * 100, y=g["iv"] * 100, mode="lines", name=name,
                                 line=dict(color=T.SERIES[i % 4], width=2),
                                 customdata=g["strike"],
                                 hovertemplate=name + "<br>strike %{customdata}<br>%{x:.1f}% from spot"
                                                      "<br>IV %{y:.2f}<extra></extra>"))
    fig.add_vline(x=0, line=dict(color=T.AXIS, width=1))
    fig.update_yaxes(title="implied vol (%)")
    fig.update_xaxes(title="strike vs spot (%)", ticksuffix="%")
    return fig


def skew_term(s: Snapshot) -> go.Figure:
    fig = _base("25-delta skew across expiries", s, hovermode="x unified")
    t = s.term.dropna(subset=["rr25"])
    if t.empty:
        return _empty(fig)
    fig.add_trace(go.Scatter(x=t["dte"], y=t["rr25"] * 100, mode="lines+markers", name="risk reversal (call - put)",
                             line=dict(color=T.SERIES[0], width=2), marker=dict(size=8),
                             hovertemplate="RR %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=t["dte"], y=t["fly25"] * 100, mode="lines+markers", name="butterfly (wings - ATM)",
                             line=dict(color=T.SERIES[1], width=2), marker=dict(size=8),
                             hovertemplate="fly %{y:.2f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=T.AXIS, width=1))
    fig.update_yaxes(title="vol points")
    fig.update_xaxes(title="days to expiry")
    return fig


def iv_surface(s: Snapshot) -> go.Figure:
    fig = _base("Implied vol surface", s, hovermode="closest")
    g = s.surface
    if g.empty:
        return _empty(fig)
    g = g.dropna(how="all")
    labels = [f"{pd.Timestamp(e):%b %d} ({d:.0f}d)" for e, d in g.index]
    x = np.round(np.asarray(g.columns, dtype=float) * 100, 1)
    z = g.to_numpy() * 100
    lo, hi = np.nanpercentile(z, [2, 95]) if np.isfinite(z).any() else (0, 1)
    fig.add_trace(go.Heatmap(z=z, x=x, y=labels, colorscale=T.SEQ_BLUE[::-1], zmin=lo, zmax=hi,
                             colorbar=dict(title=dict(text="IV %", font=dict(color=T.MUTED)),
                                           tickfont=dict(color=T.MUTED), thickness=10),
                             xgap=2, ygap=2,
                             hovertemplate="%{y}<br>%{x} from spot<br>IV %{z:.2f}<extra></extra>"))
    fig.update_xaxes(title="strike vs spot", showgrid=False, ticksuffix="%")
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_layout(margin=dict(l=110))
    return fig


def expected_move(s: Snapshot) -> go.Figure:
    fig = _base("Expected move cone (ATM straddle)", s, hovermode="x unified")
    t = s.term.copy()
    if t.empty:
        return _empty(fig)
    t["em"] = t["em_straddle"].where(np.isfinite(t["em_straddle"]), t["em_iv"])
    t = t.dropna(subset=["em"])
    x = t["expiry"]
    pct = t["em"] / s.spot * 100
    fig.add_trace(go.Scatter(x=x, y=s.spot + t["em"], mode="lines+markers", name="upper",
                             line=dict(color=T.CALL, width=2), marker=dict(size=8), customdata=pct,
                             hovertemplate="upper %{y:.2f} (+%{customdata:.2f}%)<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=s.spot - t["em"], mode="lines+markers", name="lower",
                             line=dict(color=T.PUT, width=2), marker=dict(size=8), fill="tonexty",
                             fillcolor="rgba(57,135,229,0.08)", customdata=pct,
                             hovertemplate="lower %{y:.2f} (-%{customdata:.2f}%)<extra></extra>"))
    _hline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    fig.update_yaxes(title="price")
    fig.update_xaxes(title="expiry")
    return fig


# ----------------------------------------------------------------------------
# Flow
# ----------------------------------------------------------------------------

def flow_cumulative(s: Snapshot) -> go.Figure:
    fig = _base("Cumulative premium traded (large prints)", s, hovermode="x unified")
    t = s.timeline
    if t.empty:
        return _empty(fig, "No prints yet this session")
    k, unit = _scale(np.r_[t["cum_call"], t["cum_put"]])
    x = pd.to_datetime(t["ts"]).dt.tz_convert("America/New_York").dt.tz_localize(None)
    fig.add_trace(go.Scatter(x=x, y=t["cum_call"] / k, mode="lines", name="call premium",
                             line=dict(color=T.CALL, width=2),
                             hovertemplate="calls %{y:.2f}" + unit + "<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=t["cum_put"] / k, mode="lines", name="put premium",
                             line=dict(color=T.PUT, width=2),
                             hovertemplate="puts %{y:.2f}" + unit + "<extra></extra>"))
    fig.update_yaxes(title=unit)
    fig.update_xaxes(tickformat="%H:%M")
    return fig


def flow_net(s: Snapshot) -> go.Figure:
    fig = _base("Net directional premium per 5 min (bullish +, bearish -)", s, hovermode="x")
    t = s.timeline
    if t.empty:
        return _empty(fig, "No prints yet this session")
    k, unit = _scale(t["net_bull"])
    x = pd.to_datetime(t["ts"]).dt.tz_convert("America/New_York").dt.tz_localize(None)
    y = t["net_bull"] / k
    fig.add_trace(go.Bar(x=x, y=y, marker_color=np.where(y >= 0, T.CALL, T.PUT), showlegend=False,
                         customdata=t["cum_net"] / k,
                         hovertemplate="%{x|%H:%M}<br>net %{y:.2f}" + unit +
                                       "<br>running total %{customdata:.2f}" + unit + "<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=T.AXIS, width=1))
    fig.update_yaxes(title=unit)
    fig.update_xaxes(tickformat="%H:%M")
    return fig


def flow_by_strike(s: Snapshot) -> go.Figure:
    fig = _base("Premium by strike (large prints)", s, barmode="relative")
    b = s.by_strike
    if b.empty:
        return _empty(fig, "No prints yet this session")
    k, unit = _scale(np.r_[b["call"], b["put"]])
    fig.add_trace(go.Bar(x=b["strike"], y=b["call"] / k, name="calls", marker_color=T.CALL,
                         hovertemplate="strike %{x}<br>calls %{y:.2f}" + unit + "<extra></extra>"))
    fig.add_trace(go.Bar(x=b["strike"], y=-b["put"] / k, name="puts", marker_color=T.PUT,
                         customdata=b["put"] / k,
                         hovertemplate="strike %{x}<br>puts %{customdata:.2f}" + unit + "<extra></extra>"))
    _vline(fig, s.spot, f"spot {s.spot:.2f}", T.INK)
    fig.update_yaxes(title=f"{unit} (puts below zero)")
    fig.update_xaxes(title="strike")
    fig.update_layout(hovermode="closest", bargap=0.1)
    return fig


def flow_by_dte(s: Snapshot) -> go.Figure:
    fig = _base("Premium by expiry bucket", s, barmode="group", hovermode="x unified")
    b = s.by_dte
    if b.empty or (b["call"].sum() + b["put"].sum()) == 0:
        return _empty(fig, "No prints yet this session")
    k, unit = _scale(np.r_[b["call"], b["put"]])
    fig.add_trace(go.Bar(x=b["bucket"], y=b["call"] / k, name="calls", marker_color=T.CALL,
                         hovertemplate="calls %{y:.2f}" + unit + "<extra></extra>"))
    fig.add_trace(go.Bar(x=b["bucket"], y=b["put"] / k, name="puts", marker_color=T.PUT,
                         hovertemplate="puts %{y:.2f}" + unit + "<extra></extra>"))
    fig.update_yaxes(title=unit)
    fig.update_layout(bargap=0.3, bargroupgap=0.08)
    return fig


# ----------------------------------------------------------------------------
# HTML panels
# ----------------------------------------------------------------------------

def _fmt(x, f="{:.2f}", na="n/a"):
    try:
        return f.format(x) if x is not None and np.isfinite(x) else na
    except (TypeError, ValueError):
        return na


def kpi_tiles(s: Snapshot) -> list[dict]:
    """Headline tiles as data: label, value, sub text, plus an optional colored delta."""
    m = s.metrics
    chg = m.get("chg", np.nan)
    gex = m["net_gex"]
    flip = m["flip"]
    flip_sub = (f"spot {abs(s.spot / flip - 1) * 100:.2f}% {'above' if s.spot > flip else 'below'}"
                if flip else "no flip in ±8%")
    em = m.get("em_front", np.nan)
    fe = m.get("front_expiry")
    tile = lambda label, value, sub="", delta=None, tone=None: dict(
        label=label, value=value, sub=sub, delta=delta, tone=tone)
    return [
        tile(f"{s.symbol} spot", _fmt(s.spot), "vs prev close", _fmt(chg * 100, "{:+.2f}%"),
             "up" if chg >= 0 else "dn"),
        tile("Net dealer gamma", _money(gex) + " /1%",
             "positive gamma: dampening" if gex > 0 else "negative gamma: amplifying"),
        tile("Gamma flip", _fmt(flip, "{:.1f}"), flip_sub),
        tile("Call wall / put wall", f"{_fmt(m['call_wall'], '{:.0f}')} / {_fmt(m['put_wall'], '{:.0f}')}",
             f"max pain {_fmt(m.get('max_pain'), '{:.0f}')}"),
        tile("ATM IV 30d", _fmt(m["atm_iv_30"] * 100, "{:.1f}"),
             f"RV20 {_fmt(m['rv20'] * 100, '{:.1f}')} · VRP {_fmt(m['vrp'] * 100, '{:+.1f}')}"),
        tile("Expected move (front)", "±" + _fmt(em),
             f"±{_fmt(em / s.spot * 100, '{:.2f}')}% · {pd.Timestamp(fe):%b %d}" if fe is not None else ""),
        tile("25Δ risk reversal 30d", _fmt(m["rr25_30"] * 100, "{:+.1f}"),
             f"butterfly {_fmt(m['fly25_30'] * 100, '{:+.2f}')}"),
        tile("Put / call", _fmt(m["pc_volume"]), f"premium ratio {_fmt(m['pc_premium'])}"),
        tile("Net directional flow", _money(m["net_flow"]),
             f"Δ {_money(m['net_delta_flow'])} · {m['prints']:,} prints"),
    ]


def kpi_html(s: Snapshot) -> str:
    cells = []
    for t in kpi_tiles(s):
        delta = f"<span class='od-{t['tone']}'>{t['delta']}</span> " if t["delta"] else ""
        cells.append(f"<div class='od-kpi'><div class='l'>{t['label']}</div><div class='v'>{t['value']}</div>"
                     f"<div class='s'>{delta}{t['sub']}</div></div>")
    return f"{T.CSS}<div class='od-root od-kpis'>{''.join(cells)}</div>"


_TAGS = {"bull": "BULLISH", "bear": "BEARISH", "vol": "VOL", "neutral": "STRUCTURE"}


def insight_items(s: Snapshot) -> list[tuple[str, str]]:
    """Market-read lines as (TAG, sentence)."""
    return [(_TAGS.get(tag, tag.upper()), txt) for tag, txt in s.insights]


def insights_html(s: Snapshot) -> str:
    items = "".join(f"<li><span class='od-tag'>{tag}</span>{txt}</li>" for tag, txt in insight_items(s))
    return (f"{T.CSS}<div class='od-root od-panel'><h4>Market read: {s.symbol}</h4>"
            f"<ul>{items or '<li>Waiting for data</li>'}</ul></div>")


def _table(title: str, df: pd.DataFrame, note: str = "") -> str:
    if df is None or df.empty:
        body = "<p class='od-status'>Nothing to show yet.</p>"
    else:
        head = "".join(f"<th>{c}</th>" for c in df.columns)
        rows = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>" for r in df.itertuples(index=False))
        body = f"<table class='od-table'><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    note = f"<div class='od-status' style='margin-top:6px'>{note}</div>" if note else ""
    return f"{T.CSS}<div class='od-root od-panel'><h4>{title}</h4>{body}{note}</div>"


def top_prints_table(s: Snapshot, n: int = 15):
    f = s.flow
    if f.empty:
        return "Largest prints today", None, ""
    top = f.nlargest(n, "premium")
    side = top["side"].map({1.0: "BUY", -1.0: "SELL"}).fillna("?")
    t = pd.DataFrame({
        "time (NY)": pd.to_datetime(top["ts"]).dt.tz_convert("America/New_York").dt.strftime("%H:%M:%S"),
        "contract": top["strike"].map("{:g}".format) + " " + top["type"].str.upper()
                    + " " + pd.to_datetime(top["expiry"]).dt.strftime("%b %d"),
        "side": side,
        "size": top["size"].map("{:,.0f}".format),
        "price": top["price"].map("{:.2f}".format),
        "premium": top["premium"].map(_money),
        "IV": (top["iv"] * 100).map(lambda v: _fmt(v, "{:.1f}")),
        "delta": top["delta"].map(lambda v: _fmt(v, "{:+.2f}")),
    })
    method = "quote rule (vs bid/ask)" if (s.flow["method"] == "quote").mean() > 0.5 else "tick test"
    return "Largest prints today", t, f"Side inferred by {method}. BUY call / SELL put = bullish."


def unusual_table(s: Snapshot):
    u = s.unusual
    if u is None or u.empty:
        return "Unusual activity", None, ""
    basis = u["basis"].iloc[0]
    t = pd.DataFrame({
        "contract": u["strike"].map("{:g}".format) + " " + u["type"].str.upper()
                    + " " + pd.to_datetime(u["expiry"]).dt.strftime("%b %d"),
        "volume": u["volume"].map("{:,.0f}".format),
        "OI": u["oi"].map(lambda v: _fmt(v, "{:,.0f}", "-")),
        basis: u["score"].map("{:.1f}x".format),
        "premium": u["premium_today"].map(_money),
        "IV": (u["iv"] * 100).map(lambda v: _fmt(v, "{:.1f}")),
        "delta": u["delta"].map(lambda v: _fmt(v, "{:+.2f}")),
    })
    note = ("Volume far above open interest = new positions being opened today."
            if basis == "vol/OI" else "No OI in feed: ranked by premium vs the chain median.")
    return "Unusual activity", t, note


def term_table(s: Snapshot, n: int = 10):
    t = s.term.head(n)
    if t.empty:
        return "Expiry summary", None, ""
    em = t["em_straddle"].where(np.isfinite(t["em_straddle"]), t["em_iv"])
    d = pd.DataFrame({
        "expiry": pd.to_datetime(t["expiry"]).dt.strftime("%a %b %d"),
        "DTE": t["dte"].map("{:.1f}".format),
        "ATM IV": (t["atm_iv"] * 100).map("{:.1f}".format),
        "25Δ RR": (t["rr25"] * 100).map(lambda v: _fmt(v, "{:+.1f}")),
        "25Δ fly": (t["fly25"] * 100).map(lambda v: _fmt(v, "{:+.2f}")),
        "exp. move": em.map(lambda v: _fmt(v, "±{:.2f}")),
        "exp. move %": (em / s.spot * 100).map(lambda v: _fmt(v, "±{:.2f}%")),
        "volume": t["volume"].map("{:,.0f}".format),
    })
    return "Expiry summary", d, ""


def top_prints_html(s: Snapshot) -> str:
    return _table(*top_prints_table(s))


def unusual_html(s: Snapshot) -> str:
    return _table(*unusual_table(s))


def term_table_html(s: Snapshot) -> str:
    return _table(*term_table(s))


def _tidy(fn):
    """Charts with fewer than two legend entries drop the legend band above the plot."""
    def build(s: Snapshot) -> go.Figure:
        fig = fn(s)
        shown = [t for t in fig.data if t.showlegend is not False]
        if len(shown) < 2 or fig.layout.showlegend is False:
            fig.update_layout(showlegend=False, margin=dict(t=48))
        return fig
    build.__name__ = fn.__name__
    build.__doc__ = fn.__doc__
    return build


FIGURES = {
    "levels": ("Price & Levels", _tidy(price_levels)),
    "expected_move": ("Price & Levels", _tidy(expected_move)),
    "gex": ("Dealer Positioning", _tidy(gex_by_strike)),
    "profile": ("Dealer Positioning", _tidy(gamma_profile)),
    "positioning": ("Dealer Positioning", _tidy(positioning)),
    "vanna": ("Dealer Positioning", _tidy(vanna_by_strike)),
    "charm": ("Dealer Positioning", _tidy(charm_by_strike)),
    "term": ("Volatility", _tidy(term_structure)),
    "smile": ("Volatility", _tidy(smile)),
    "skew": ("Volatility", _tidy(skew_term)),
    "surface": ("Volatility", _tidy(iv_surface)),
    "flow_cum": ("Options Flow", _tidy(flow_cumulative)),
    "flow_net": ("Options Flow", _tidy(flow_net)),
    "flow_strike": ("Options Flow", _tidy(flow_by_strike)),
    "flow_dte": ("Options Flow", _tidy(flow_by_dte)),
}

TABLES = {
    "term_table": ("Volatility", term_table_html),
    "top_prints": ("Options Flow", top_prints_html),
    "unusual": ("Options Flow", unusual_html),
}

TABS = ["Price & Levels", "Dealer Positioning", "Volatility", "Options Flow"]
