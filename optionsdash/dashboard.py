"""Live Jupyter dashboard: ipywidgets layout + Plotly FigureWidgets on a refresh loop.

    dash = LiveDashboard(feed, symbols=["SPY", "QQQ"], refresh_seconds=15)
    dash.show()      # renders and starts the live loop
    dash.stop()      # stops refreshing

The loop is an asyncio task on the notebook kernel's own event loop, so the
notebook stays usable while the dashboard updates. Network calls run in a
worker thread so they never freeze the UI.
"""

from __future__ import annotations

import asyncio
import html
import time
import traceback
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import charts as C
from . import theme as T
from .analytics import Snapshot, build_snapshot


def compute_snapshot(feed, symbol: str, chain: pd.DataFrame | None = None) -> Snapshot:
    """Pull everything for one symbol and run the analytics."""
    chain = feed.chain(symbol) if chain is None else chain
    flow = feed.flow(symbol)
    intraday = feed.intraday(symbol)
    daily = feed.daily(symbol)
    return build_snapshot(symbol, chain, flow, intraday, daily, live_spot=feed.live_spot(symbol))


def _sync(fw: go.FigureWidget, fig: go.Figure):
    """Copy a freshly built figure into a live FigureWidget with minimal flicker."""
    same = (len(fw.data) == len(fig.data)
            and all(a.type == b.type for a, b in zip(fw.data, fig.data)))
    if not same:
        fw.data = ()
        fw.add_traces(list(fig.data))
    lay = fig.layout.to_plotly_json()
    lay.setdefault("shapes", [])
    lay.setdefault("annotations", [])
    with fw.batch_update():
        if same:
            for a, b in zip(fw.data, fig.data):
                a.update(b.to_plotly_json(), overwrite=True)
        fw.layout.update(lay, overwrite=True)


class LiveDashboard:
    def __init__(self, feed, symbols=("SPY", "QQQ"), refresh_seconds: int = 15,
                 chain_refresh_seconds: int = 60):
        self.feed = feed
        self.symbols = [s.upper() for s in symbols]
        self.symbol = self.symbols[0]
        self.refresh_seconds = refresh_seconds
        self.chain_refresh_seconds = chain_refresh_seconds
        self._chain_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self.snapshots: dict[str, Snapshot] = {}
        self._task = None
        self._wake = None
        self._running = False
        self._ui = None
        self._sized = False
        self.last_error = None

    # ------------------------------------------------------------------ data
    def _chain(self, symbol):
        ts, df = self._chain_cache.get(symbol, (0.0, None))
        if df is None or time.time() - ts >= self.chain_refresh_seconds:
            df = self.feed.chain(symbol)
            self._chain_cache[symbol] = (time.time(), df)
        return df

    def snapshot(self, symbol: str | None = None) -> Snapshot:
        symbol = (symbol or self.symbol).upper()
        snap = compute_snapshot(self.feed, symbol, chain=self._chain(symbol))
        self.snapshots[symbol] = snap
        return snap

    # -------------------------------------------------------------------- UI
    def _build_ui(self):
        import ipywidgets as W

        self._w = W
        self.title = W.HTML()
        self.status = W.HTML()
        self.sym_toggle = W.ToggleButtons(options=self.symbols, value=self.symbol,
                                          layout=W.Layout(width="auto"))
        self.sym_toggle.style.button_width = "70px"
        self.pause_btn = W.Button(description="Pause", icon="pause", layout=W.Layout(width="100px"))
        self.refresh_dd = W.Dropdown(options=[5, 10, 15, 30, 60, 120], value=self.refresh_seconds,
                                     description="every (s)", layout=W.Layout(width="160px"),
                                     style={"description_width": "60px"})
        self.sym_toggle.observe(self._on_symbol, names="value")
        self.pause_btn.on_click(self._on_pause)
        self.refresh_dd.observe(self._on_refresh, names="value")

        self.kpis = W.HTML()
        self.insights = W.HTML()
        self.figs = {k: go.FigureWidget(layout=dict(template="optionsdash", height=C.HEIGHT))
                     for k in C.FIGURES}
        self.tables = {k: W.HTML() for k in C.TABLES}
        self.tape = W.HTML()

        grid = lambda kids: W.GridBox(kids, layout=W.Layout(
            grid_template_columns="repeat(auto-fill, minmax(460px, 1fr))", grid_gap="8px", width="100%"))
        pages = []
        for tab in C.TABS:
            figs = [self.figs[k] for k, (t, _) in C.FIGURES.items() if t == tab]
            tabs_ = [self.tables[k] for k, (t, _) in C.TABLES.items() if t == tab]
            if tab == "Options Flow":
                kids = [grid(figs), grid(tabs_)]
                if getattr(self.feed, "live", False) and self.feed.use_websocket:
                    kids.append(self.tape)
            else:
                kids = [grid(figs)] + tabs_
            pages.append(W.VBox(kids))
        self.tabs = W.Tab(children=pages)
        for i, t in enumerate(C.TABS):
            self.tabs.set_title(i, t)
        self.tabs.observe(self._on_tab, names="selected_index")

        bar = W.HBox([self.title, self.sym_toggle, self.pause_btn, self.refresh_dd],
                     layout=W.Layout(align_items="center", justify_content="flex-start",
                                     flex_flow="row wrap", grid_gap="12px"))
        root = W.VBox([bar, self.status, self.kpis, self.insights, self.tabs],
                      layout=W.Layout(width="100%", grid_gap="8px"))
        root.add_class("od-box")
        self.title.value = (f"{T.CSS}<div class='od-root od-title'>Options Quant Dashboard"
                            f" <span class='od-status'>· {html.escape(self.feed.source)}</span></div>")
        self._ui = root
        return root

    def _set_status(self, msg: str, kind: str = "live"):
        self.status.value = f"{T.CSS}<div class='od-root od-status'><span class='od-{kind}'>●</span> {msg}</div>"

    def _render(self, snap: Snapshot):
        self.kpis.value = C.kpi_html(snap)
        self.insights.value = C.insights_html(snap)
        for k, (_, fn) in C.FIGURES.items():
            try:
                _sync(self.figs[k], fn(snap))
            except Exception as e:  # one broken chart must not kill the rest
                self.last_error = traceback.format_exc()
                self.figs[k].layout.title = f"{k}: {e}"
        for k, (_, fn) in C.TABLES.items():
            self.tables[k].value = fn(snap)
        if getattr(self.feed, "live", False) and self.feed.use_websocket:
            tape = self.feed.live_tape(snap.symbol)
            if not tape.empty:
                t = tape.tail(20).iloc[::-1]
                t = pd.DataFrame({
                    "time": pd.to_datetime(t["ts"], utc=True, errors="coerce").dt.tz_convert(
                        "America/New_York").dt.strftime("%H:%M:%S"),
                    "contract": t["strike"].map("{:g}".format) + " " + t["type"].str.upper() + " "
                                + pd.to_datetime(t["expiry"]).dt.strftime("%b %d"),
                    "price": t["price"].map("{:.2f}".format),
                    "size": t["size"].map(lambda v: f"{v:,.0f}" if v == v and v is not None else "-"),
                    "notional": t["premium"].map(lambda v: C._money(v) if v == v and v is not None else "-"),
                })
            self.tape.value = C._table(f"Live tape (WebSocket: {self.feed.ws_status})",
                                       t if not tape.empty else None)

    # ------------------------------------------------------------------ loop
    async def _loop(self):
        loop = asyncio.get_running_loop()
        while self._running:
            sym = self.symbol
            t0 = time.time()
            self._set_status(f"updating {sym}…", "live" if self.feed.live else "demo")
            try:
                snap = await loop.run_in_executor(None, self.snapshot, sym)
                if sym == self.symbol:
                    self._render(snap)
                    if not self._sized:  # the container's CSS lands with the first render
                        self._sized = True
                        self._on_tab({"new": self.tabs.selected_index})
                kind = "live" if self.feed.live else "demo"
                label = "LIVE" if self.feed.live else "DEMO DATA — paste your API key to go live"
                ws = f" · websocket {self.feed.ws_status}" if self.feed.live and self.feed.use_websocket else ""
                self._set_status(
                    f"<b>{label}</b> · {sym} updated {datetime.now():%H:%M:%S} "
                    f"({time.time() - t0:.1f}s) · {snap.metrics['contracts']:,} contracts, "
                    f"{snap.metrics['prints']:,} prints · refresh every {self.refresh_seconds}s{ws}", kind)
            except Exception as e:
                self.last_error = traceback.format_exc()
                self._set_status(f"<b>update failed:</b> {html.escape(str(e))[:300]} "
                                 f"· retrying in {self.refresh_seconds}s "
                                 f"(see <code>dash.last_error</code>)", "err")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.refresh_seconds)
            except asyncio.TimeoutError:
                pass

    def _on_tab(self, change):
        """Charts in a hidden tab are laid out at a default width; re-measure when shown."""
        tab = C.TABS[change["new"]]
        for k, (t, _) in C.FIGURES.items():
            if t == tab:
                fw = self.figs[k]
                fw.layout.autosize = False
                fw.layout.autosize = True

    def _on_symbol(self, change):
        self.symbol = change["new"]
        if self.symbol in self.snapshots:
            self._render(self.snapshots[self.symbol])
        if self._wake:
            self._wake.set()

    def _on_refresh(self, change):
        self.refresh_seconds = int(change["new"])
        if self._wake:
            self._wake.set()

    def _on_pause(self, _):
        if self._running:
            self.stop()
        else:
            self.start()

    # ---------------------------------------------------------------- public
    def start(self):
        if self._ui is None:
            self._build_ui()
        if self._running:
            return
        self._running = True
        self._wake = asyncio.Event()
        self.feed.start_stream(self.symbols)
        self._task = asyncio.get_event_loop().create_task(self._loop())
        self.pause_btn.description, self.pause_btn.icon = "Pause", "pause"

    def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._ui is not None:
            self.pause_btn.description, self.pause_btn.icon = "Resume", "play"
            self._set_status("paused", "demo")

    def show(self):
        """Display the dashboard in the notebook and start the live loop."""
        from IPython.display import display

        ui = self._build_ui() if self._ui is None else self._ui
        display(ui)
        self.start()
        return None

    # ------------------------------------------------------- static outputs
    def figures(self, symbol: str | None = None) -> dict:
        snap = self.snapshots.get((symbol or self.symbol).upper()) or self.snapshot(symbol)
        return {k: fn(snap) for k, (_, fn) in C.FIGURES.items()}

    def show_static(self, symbol: str | None = None):
        """Plain (non-widget) render, for front-ends without ipywidgets support."""
        from IPython.display import HTML, display

        snap = self.snapshot(symbol)
        display(HTML(C.kpi_html(snap)))
        display(HTML(C.insights_html(snap)))
        for k, (_, fn) in C.FIGURES.items():
            fn(snap).show()
        for k, (_, fn) in C.TABLES.items():
            display(HTML(fn(snap)))

    def save_html(self, path: str = "options_dashboard.html", symbols=None, offline: bool = False) -> str:
        """Write an HTML report (all charts + tables) you can share.

        offline=True embeds plotly.js (~4MB) so the file opens without internet.
        """
        from plotly.offline import get_plotlyjs, get_plotlyjs_version

        # plotly.js goes in <head>, so every chart sizes itself to its grid cell
        head_js = (f"<script>{get_plotlyjs()}</script>" if offline else
                   f"<script src='https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js'></script>")
        parts = []
        for sym in symbols or self.symbols:
            snap = self.snapshot(sym)
            parts.append(f"<h2 style='color:{T.INK};font-family:{T.FONT}'>{sym}</h2>")
            parts.append(C.kpi_html(snap))
            parts.append("<div style='height:8px'></div>" + C.insights_html(snap))
            for tab in C.TABS:
                parts.append(f"<h3 style='color:{T.INK_2};font-family:{T.FONT}'>{tab}</h3>"
                             "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:8px'>")
                for k, (t, fn) in C.FIGURES.items():
                    if t == tab:
                        chart = fn(snap).to_html(full_html=False, include_plotlyjs=False, default_width="100%",
                                                 config={"responsive": True, "displaylogo": False})
                        parts.append(f"<div style='min-width:0'>{chart}</div>")
                parts.append("</div>")
                for k, (t, fn) in C.TABLES.items():
                    if t == tab:
                        parts.append(fn(snap))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        doc = (f"<!doctype html><html><head><meta charset='utf-8'><title>Options Quant Dashboard</title>"
               f"<meta name='viewport' content='width=device-width, initial-scale=1'>{head_js}</head>"
               f"<body style='background:{T.PAGE};padding:16px;margin:0'>"
               f"<div style='color:{T.MUTED};font-family:{T.FONT}'>Snapshot {stamp} · {html.escape(self.feed.source)}</div>"
               + "".join(parts)
               + "<script>window.addEventListener('load',()=>document.querySelectorAll('.js-plotly-plot')"
                 ".forEach(g=>Plotly.Plots.resize(g)));</script></body></html>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)
        return path
