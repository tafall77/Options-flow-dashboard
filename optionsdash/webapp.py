"""Browser version of the dashboard (Dash). Start it with `python run_dashboard.py`.

Same feed, analytics and charts as the notebook, laid out as a full-screen page:
a sticky control bar, a headline strip, the market read in a side column and the
charts in tabs, several per row on wide screens.
"""

from __future__ import annotations

import threading
import time
import traceback
import webbrowser
from datetime import datetime

import pandas as pd
from dash import Dash, Input, Output, State, dcc, html, no_update

from . import charts as C
from . import theme as T
from .dashboard import LiveDashboard
from .feed import make_feed

GRAPH_CONFIG = {"displaylogo": False, "responsive": True,
                "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}
REFRESH_CHOICES = [5, 10, 15, 30, 60, 120]

PAGE_CSS = f"""
html, body {{ margin: 0; background: {T.PAGE}; color: {T.INK_2}; font-family: {T.FONT}; }}
* {{ box-sizing: border-box; }}
.bar {{ position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px;
       padding: 10px 16px; background: {T.PAGE}; border-bottom: 1px solid {T.AXIS}; }}
.bar .brand {{ font-size: 17px; font-weight: 600; color: {T.INK}; }}
.bar .src {{ font-size: 12px; color: {T.MUTED}; margin-left: 6px; font-weight: 400; }}
.bar .grow {{ flex: 1 1 auto; }}
.seg {{ display: inline-flex; border: 1px solid {T.AXIS}; border-radius: 8px; overflow: hidden; }}
.seg label {{ margin: 0 !important; padding: 5px 12px; cursor: pointer; font-size: 13px; color: {T.INK_2};
             border-right: 1px solid {T.AXIS}; user-select: none; display: inline-flex; align-items: center; }}
.seg label:last-child {{ border-right: 0; }}
.seg input {{ display: none; }}
.seg label:has(input:checked) {{ background: {T.AXIS}; color: {T.INK}; }}
.ctl {{ display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: {T.MUTED}; }}
.btn {{ background: {T.SURFACE}; color: {T.INK}; border: 1px solid {T.AXIS}; border-radius: 8px; padding: 5px 14px;
       font-size: 13px; cursor: pointer; font-family: {T.FONT}; }}
.btn:hover {{ border-color: {T.MUTED}; }}
.status {{ padding: 6px 16px; font-size: 12px; color: {T.MUTED}; min-height: 26px; }}
.wrap {{ padding: 0 16px 24px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 8px; margin-bottom: 10px; }}
.main {{ display: grid; grid-template-columns: minmax(300px, 360px) minmax(0, 1fr); gap: 10px; align-items: start; }}
.main aside {{ position: sticky; top: 62px; }}
@media (max-width: 1100px) {{ .main {{ grid-template-columns: minmax(0, 1fr); }} .main aside {{ position: static; }} }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 8px; margin-top: 8px; }}
@media (max-width: 520px) {{ .grid {{ grid-template-columns: minmax(0, 1fr); }} }}
.card {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 8px; overflow: hidden; min-width: 0; }}
.tables {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(560px, 1fr)); gap: 8px; margin-top: 8px; }}
@media (max-width: 620px) {{ .tables {{ grid-template-columns: minmax(0, 1fr); }} }}
.tables .od-panel {{ overflow-x: auto; }}
.od-tabs {{ border-bottom: 1px solid {T.AXIS} !important; }}
.od-tab {{ background: {T.PAGE} !important; color: {T.MUTED} !important; border: 0 !important;
          border-bottom: 2px solid transparent !important; padding: 8px 14px !important; font-size: 13px; }}
.od-tab--selected {{ color: {T.INK} !important; border-bottom: 2px solid {T.CALL} !important;
                    background: {T.PAGE} !important; }}
.foot {{ margin-top: 14px; font-size: 11px; color: {T.MUTED}; }}
.od-panel {{ font-size: 13px; }}
.kpis .od-kpi .v {{ font-size: 20px; }}
.kpis .od-kpi .s {{ font-size: 11.5px; }}
"""


def _tiles(snap):
    out = []
    for t in C.kpi_tiles(snap):
        sub = [html.Span(t["delta"] + " ", className=f"od-{t['tone']}")] if t["delta"] else []
        out.append(html.Div([html.Div(t["label"], className="l"), html.Div(t["value"], className="v"),
                             html.Div(sub + [t["sub"]], className="s")], className="od-kpi"))
    return out


def _market_read(snap):
    items = [html.Li([html.Span(tag, className="od-tag"), txt]) for tag, txt in C.insight_items(snap)]
    return html.Div([html.H4(f"Market read: {snap.symbol}"), html.Ul(items or [html.Li("Waiting for data")])],
                    className="od-panel")


def _table(title, df, note=""):
    if df is None or df.empty:
        body = html.P("Nothing to show yet.", className="od-status")
    else:
        head = html.Thead(html.Tr([html.Th(c) for c in df.columns]))
        rows = html.Tbody([html.Tr([html.Td(v) for v in r]) for r in df.itertuples(index=False)])
        body = html.Table([head, rows], className="od-table")
    kids = [html.H4(title), body]
    if note:
        kids.append(html.Div(note, className="od-status", style={"marginTop": "6px"}))
    return html.Div(kids, className="od-panel")


def _tape(feed, symbol):
    tape = feed.live_tape(symbol)
    if tape.empty:
        return _table(f"Live tape (WebSocket: {feed.ws_status})", None)
    t = tape.tail(20).iloc[::-1]
    t = pd.DataFrame({
        "time": pd.to_datetime(t["ts"], utc=True, errors="coerce").dt.tz_convert("America/New_York").dt.strftime("%H:%M:%S"),
        "contract": t["strike"].map("{:g}".format) + " " + t["type"].str.upper() + " "
                    + pd.to_datetime(t["expiry"]).dt.strftime("%b %d"),
        "price": t["price"].map("{:.2f}".format),
        "size": t["size"].map(lambda v: f"{v:,.0f}" if v == v and v is not None else "-"),
        "notional": t["premium"].map(lambda v: C._money(v) if v == v and v is not None else "-"),
    })
    return _table(f"Live tape (WebSocket: {feed.ws_status})", t)


TABLE_FUNCS = {"term_table": C.term_table, "top_prints": C.top_prints_table, "unusual": C.unusual_table}


def create_app(feed, symbols=("SPY", "QQQ"), refresh_seconds: int = 15, chain_refresh_seconds: int = 60) -> Dash:
    symbols = [s.upper() for s in symbols]
    data = LiveDashboard(feed, symbols=symbols, refresh_seconds=refresh_seconds,
                         chain_refresh_seconds=chain_refresh_seconds)
    lock = threading.Lock()
    has_tape = bool(getattr(feed, "live", False) and getattr(feed, "use_websocket", False))
    feed.start_stream(symbols)

    app = Dash(__name__, title="Options Quant Dashboard", update_title=None)
    app.index_string = app.index_string.replace(
        "{%css%}", "{%css%}" + T.CSS + f"<style>{PAGE_CSS}</style>")

    def graph(k):
        return html.Div(dcc.Graph(id=f"g-{k}", config=GRAPH_CONFIG, style={"height": f"{C.HEIGHT}px"},
                                  figure={"layout": {"template": "optionsdash", "height": C.HEIGHT,
                                                     "paper_bgcolor": T.SURFACE, "plot_bgcolor": T.SURFACE}}),
                        className="card")

    tabs = []
    for tab in C.TABS:
        kids = [html.Div([graph(k) for k, (t, _) in C.FIGURES.items() if t == tab], className="grid")]
        tables = [html.Div(id=f"t-{k}") for k, (t, _) in C.TABLES.items() if t == tab]
        if tab == "Options Flow" and has_tape:
            tables.append(html.Div(id="tape"))
        if tables:
            kids.append(html.Div(tables, className="tables"))
        tabs.append(dcc.Tab(label=tab, value=tab, children=kids,
                            className="od-tab", selected_className="od-tab--selected"))

    app.layout = html.Div([
        html.Div([
            html.Div(["Options Quant Dashboard", html.Span(f"· {feed.source}", className="src")], className="brand"),
            dcc.RadioItems(id="symbol", options=symbols, value=symbols[0], inline=True, className="seg"),
            html.Div(className="grow"),
            html.Div(["refresh",
                      dcc.RadioItems(id="refresh", options=[{"label": f"{s}s", "value": s} for s in REFRESH_CHOICES],
                                     value=refresh_seconds, inline=True, className="seg")], className="ctl"),
            html.Button("Pause", id="pause", n_clicks=0, className="btn"),
        ], className="bar"),
        html.Div(id="status", className="status"),
        html.Div([
            html.Div(id="kpis", className="kpis"),
            html.Div([
                html.Aside(html.Div(id="read")),
                html.Section(dcc.Tabs(id="tabs", value=C.TABS[0], children=tabs, className="od-tabs")),
            ], className="main"),
            html.Div("Dealer exposure uses the street convention (dealers long call gamma, short put gamma): "
                     "a model estimate of positioning, not a view of real dealer books. Not trading advice.",
                     className="foot"),
        ], className="wrap"),
        dcc.Interval(id="tick", interval=refresh_seconds * 1000),
    ])

    @app.callback(Output("tick", "interval"), Input("refresh", "value"))
    def set_refresh(seconds):
        return int(seconds) * 1000

    @app.callback(Output("tick", "disabled"), Output("pause", "children"), Input("pause", "n_clicks"))
    def toggle_pause(n):
        paused = bool(n and n % 2)
        return paused, ("Resume" if paused else "Pause")

    outputs = ([Output("status", "children"), Output("kpis", "children"), Output("read", "children")]
               + [Output(f"g-{k}", "figure") for k in C.FIGURES]
               + [Output(f"t-{k}", "children") for k in C.TABLES]
               + ([Output("tape", "children")] if has_tape else []))
    n_out = len(outputs)

    @app.callback(*outputs, Input("tick", "n_intervals"), Input("symbol", "value"), State("refresh", "value"))
    def update(_n, symbol, refresh):
        t0 = time.time()
        try:
            with lock:  # one pull at a time, even if a refresh lands while the last is running
                snap = data.snapshot(symbol)
        except Exception as e:
            data.last_error = traceback.format_exc()
            print(data.last_error)
            msg = html.Span([html.Span("● ", className="od-err"),
                             html.B("Update failed: "), str(e)[:300], f" · retrying in {refresh}s"])
            return [msg] + [no_update] * (n_out - 1)

        live = getattr(feed, "live", False)
        label = "LIVE" if live else "DEMO DATA: paste your API key in run_dashboard.py to go live"
        ws = f" · websocket {feed.ws_status}" if has_tape else ""
        status = html.Span([html.Span("● ", className="od-live" if live else "od-demo"), html.B(label),
                            f" · {symbol} updated {datetime.now():%H:%M:%S} ({time.time() - t0:.1f}s)"
                            f" · {snap.metrics['contracts']:,} contracts, {snap.metrics['prints']:,} prints"
                            f" · refresh every {refresh}s{ws}"])
        figs = []
        for k, (_, fn) in C.FIGURES.items():
            try:
                figs.append(fn(snap))
            except Exception:
                data.last_error = traceback.format_exc()
                figs.append(no_update)
        tables = [_table(*TABLE_FUNCS[k](snap)) for k in C.TABLES]
        extra = [_tape(feed, symbol)] if has_tape else []
        return [status, _tiles(snap), _market_read(snap)] + figs + tables + extra

    return app


def run(api_key: str | None = None, symbols=("SPY", "QQQ"), refresh_seconds: int = 15,
        chain_refresh_seconds: int = 60, max_dte: int = 60, flow_min_premium: float = 25_000,
        use_websocket: bool = True, port: int = 8050, open_browser: bool = True):
    """Start the web dashboard and open it in the default browser."""
    feed = make_feed(api_key, max_dte=max_dte, flow_min_premium=flow_min_premium, use_websocket=use_websocket)
    app = create_app(feed, symbols, refresh_seconds, chain_refresh_seconds)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Options Quant Dashboard ({feed.source})\n  Open {url} in your browser. Press Ctrl+C here to stop.\n")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False)
