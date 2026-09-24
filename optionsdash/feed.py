"""Data feeds: the live London Strategic Edge (LSE) feed and an offline demo feed.

Both feeds expose the same four methods, so the analytics and the dashboard
never care where the data came from:

    chain(symbol)          -> DataFrame, one row per option contract
    flow(symbol)           -> DataFrame, one row per option print (time & sales)
    intraday(symbol)       -> DataFrame of 1 minute underlying bars for today
    daily(symbol)          -> DataFrame of daily underlying bars (for realized vol)

LSE field names are mapped onto one internal schema in ``normalize_chain`` /
``normalize_flow``, so a renamed or missing column degrades gracefully instead
of crashing the dashboard.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")

CHAIN_COLUMNS = ["ticker", "underlying", "type", "strike", "expiry", "dte", "T",
                 "last", "bid", "ask", "mid", "iv", "delta", "gamma", "theta", "vega",
                 "volume", "premium_today", "oi", "spot"]
FLOW_COLUMNS = ["ts", "ticker", "underlying", "type", "strike", "expiry", "dte", "T",
                "price", "size", "premium", "iv", "delta", "gamma", "bid", "ask", "spot"]

_ALIASES = {
    "ticker": ["ticker", "symbol", "contract", "option_symbol", "osi", "option_ticker"],
    "type": ["type", "right", "option_type", "contract_type", "put_call", "cp"],
    "strike": ["strike", "strike_price"],
    "expiry": ["expiry", "expiration", "expiration_date", "expiry_date", "exp"],
    "last": ["last_price", "last", "close", "mark", "price"],
    "bid": ["bid", "bid_price"],
    "ask": ["ask", "ask_price"],
    "iv": ["iv", "implied_volatility", "impliedvolatility", "iv_avg"],
    "delta": ["delta", "delta_avg"],
    "gamma": ["gamma", "gamma_avg"],
    "theta": ["theta", "theta_avg"],
    "vega": ["vega", "vega_avg"],
    "volume": ["volume_today", "volume", "day_volume", "daily_volume"],
    "premium_today": ["premium_today", "notional_today", "premium"],
    "oi": ["open_interest", "oi", "openinterest"],
    "spot": ["underlying_price", "spot", "underlying_last", "stock_price", "underlying_px"],
    "ts": ["ts", "timestamp", "time", "datetime", "trade_time", "last_trade_at", "updated_at"],
    # flow-only
    "price": ["price", "trade_price", "last_price", "fill_price"],
    "size": ["size", "volume", "qty", "quantity", "contracts"],
    "premium": ["premium", "notional", "value", "premium_usd"],
}

def _empty(columns) -> pd.DataFrame:
    """A zero-row frame with real dtypes, so concatenating it never degrades numeric columns."""
    dt = {"ts": "datetime64[ns, UTC]", "expiry": "datetime64[ns]", "ticker": object,
          "underlying": object, "type": object}
    return pd.DataFrame({c: pd.Series(dtype=dt.get(c, "float64")) for c in columns})


_OSI = re.compile(r"^(?:O:)?([A-Z][A-Z0-9.]{0,9}?)(\d{6})([CP])(\d{8})$")


# ----------------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------------

def _pick(df: pd.DataFrame, field: str) -> pd.Series:
    for name in _ALIASES[field]:
        if name in df.columns:
            return df[name]
    return pd.Series(np.nan, index=df.index)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _parse_osi(tickers: pd.Series) -> pd.DataFrame:
    parts = tickers.astype(str).str.upper().str.extract(_OSI)
    out = pd.DataFrame(index=tickers.index)
    out["underlying"] = parts[0]
    out["expiry"] = pd.to_datetime(parts[1], format="%y%m%d", errors="coerce")
    out["type"] = parts[2].map({"C": "call", "P": "put"})
    out["strike"] = _num(parts[3]) / 1000.0
    return out


def _norm_type(s: pd.Series) -> pd.Series:
    first = s.astype(str).str.strip().str.lower().str[:1]
    return first.map({"c": "call", "p": "put"})


def _time_to_expiry(expiry: pd.Series, now: datetime | None = None):
    """Days and years until the 4pm New York close on the expiry date."""
    now = now or datetime.now(timezone.utc)
    exp = pd.to_datetime(expiry, errors="coerce")
    close = (exp.dt.tz_localize(None).dt.normalize() + pd.Timedelta(hours=16)).dt.tz_localize(
        NY, nonexistent="shift_forward", ambiguous=False)
    days = (close - pd.Timestamp(now)).dt.total_seconds() / 86400.0
    days = days.clip(lower=1.0 / 1440)  # never below one minute
    return days, days / 365.0


def normalize_chain(rows, symbol: str, spot_hint: float | None = None) -> pd.DataFrame:
    """Map raw chain rows (list of dicts or DataFrame) onto CHAIN_COLUMNS."""
    raw = pd.DataFrame(rows)
    if raw.empty:
        return _empty(CHAIN_COLUMNS)
    df = pd.DataFrame(index=raw.index)
    df["ticker"] = _pick(raw, "ticker").astype(str)
    osi = _parse_osi(df["ticker"])
    df["underlying"] = osi["underlying"].fillna(symbol.upper())
    df["type"] = _norm_type(_pick(raw, "type")).fillna(osi["type"])
    df["strike"] = _num(_pick(raw, "strike")).fillna(osi["strike"])
    df["expiry"] = pd.to_datetime(_pick(raw, "expiry"), errors="coerce", utc=True).dt.tz_localize(None)
    df["expiry"] = df["expiry"].dt.normalize().fillna(osi["expiry"])
    for f in ("last", "bid", "ask", "iv", "delta", "gamma", "theta", "vega",
              "volume", "premium_today", "oi", "spot"):
        df[f] = _num(_pick(raw, f))
    df = df.dropna(subset=["type", "strike", "expiry"])
    if df.empty:
        return _empty(CHAIN_COLUMNS)

    # IV sometimes arrives in percent.
    if df["iv"].median(skipna=True) > 3:
        df["iv"] = df["iv"] / 100.0
    df.loc[(df["iv"] <= 0.005) | (df["iv"] > 5), "iv"] = np.nan

    has_quote = df["bid"].gt(0) & df["ask"].gt(0) & df["ask"].ge(df["bid"])
    df["mid"] = np.where(has_quote, (df["bid"] + df["ask"]) / 2, df["last"])
    df["volume"] = df["volume"].fillna(0.0)
    df["premium_today"] = df["premium_today"].fillna(df["volume"] * df["last"].fillna(0) * 100)

    spot = df["spot"].median(skipna=True)
    if not np.isfinite(spot) or spot <= 0:
        spot = spot_hint if spot_hint else np.nan
    df["spot"] = spot
    df["dte"], df["T"] = _time_to_expiry(df["expiry"])
    return df[CHAIN_COLUMNS].sort_values(["expiry", "strike", "type"]).reset_index(drop=True)


def normalize_flow(rows, symbol: str) -> pd.DataFrame:
    """Map raw option prints onto FLOW_COLUMNS. `premium` is dollars traded."""
    raw = pd.DataFrame(rows)
    if raw.empty:
        return _empty(FLOW_COLUMNS)
    df = pd.DataFrame(index=raw.index)
    df["ts"] = pd.to_datetime(_pick(raw, "ts"), errors="coerce", utc=True)
    df["ticker"] = _pick(raw, "ticker").astype(str)
    osi = _parse_osi(df["ticker"])
    df["underlying"] = osi["underlying"].fillna(symbol.upper())
    df["type"] = _norm_type(_pick(raw, "type")).fillna(osi["type"])
    df["strike"] = _num(_pick(raw, "strike")).fillna(osi["strike"])
    df["expiry"] = pd.to_datetime(_pick(raw, "expiry"), errors="coerce", utc=True).dt.tz_localize(None)
    df["expiry"] = df["expiry"].dt.normalize().fillna(osi["expiry"])
    for f in ("price", "size", "premium", "iv", "delta", "gamma", "bid", "ask", "spot"):
        df[f] = _num(_pick(raw, f))
    # Fill whichever of price / size / premium is missing from the other two.
    df["premium"] = df["premium"].fillna(df["price"] * df["size"] * 100)
    df["size"] = df["size"].fillna(df["premium"] / (df["price"] * 100))
    df["price"] = df["price"].fillna(df["premium"] / (df["size"] * 100))
    if df["iv"].median(skipna=True) > 3:
        df["iv"] = df["iv"] / 100.0
    df = df.dropna(subset=["ts", "type", "strike", "expiry", "premium"])
    if df.empty:
        return _empty(FLOW_COLUMNS)
    df["dte"], df["T"] = _time_to_expiry(df["expiry"])
    return df[FLOW_COLUMNS].sort_values("ts").reset_index(drop=True)


def _bars(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    ts = next((c for c in ("timestamp", "ts", "minute", "time", "datetime") if c in df.columns), None)
    out = pd.DataFrame({"ts": pd.to_datetime(df[ts], errors="coerce", utc=True)})
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = _num(df[c]) if c in df.columns else np.nan
    return out.dropna(subset=["ts", "close"]).sort_values("ts").reset_index(drop=True)


def session_start_utc(now: datetime | None = None) -> datetime:
    """Most recent 09:30 New York open at or before now (skips weekends)."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    day = now.date()
    if now.time() < datetime.strptime("09:30", "%H:%M").time():
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY).astimezone(timezone.utc)


# ----------------------------------------------------------------------------
# Live LSE feed
# ----------------------------------------------------------------------------

class LSEFeed:
    """Live option data from London Strategic Edge.

    REST (polled): option chain with IV + greeks + today's volume, option
    prints with premium, 1m and daily underlying candles.
    WebSocket (optional, background thread): real-time underlying price and a
    real-time tape of option prints.

    Args:
        api_key: your LSE key.
        max_dte: furthest expiry (calendar days) pulled into the chain.
        strike_band: strikes within +/- this fraction of spot are pulled.
        flow_min_premium: only prints at or above this dollar premium are
            pulled into the flow panels (SPY/QQQ print millions of small
            trades a day; the institutional tape is what carries signal).
        use_websocket: stream spot + option ticks in real time.
    """

    live = True
    source = "London Strategic Edge"

    def __init__(self, api_key: str, max_dte: int = 60, strike_band: float = 0.12,
                 flow_min_premium: float = 25_000, use_websocket: bool = True,
                 timeout: int = 60):
        from lse import LSE  # imported here so demo mode works without the package

        self._LSE = LSE
        self.api_key = api_key
        self.client = LSE(api_key=api_key, timeout=timeout)
        self.max_dte = int(max_dte)
        self.strike_band = float(strike_band)
        self.flow_min_premium = flow_min_premium
        self.use_websocket = use_websocket
        self._flow: dict[str, pd.DataFrame] = {}
        self._daily: dict[str, tuple[str, pd.DataFrame]] = {}
        self._last_spot: dict[str, float] = {}
        self._ws_client = None
        self._ws_thread = None
        self.ws_status = "off"
        self.tape: dict[str, deque] = {}

    # -- WebSocket ----------------------------------------------------------
    def start_stream(self, symbols):
        """Start a daemon thread streaming underlying + option ticks."""
        if not self.use_websocket or self._ws_thread is not None:
            return
        syms = [s.upper() for s in symbols]
        for s in syms:
            self.tape.setdefault(s, deque(maxlen=500))
        ws = self._LSE(api_key=self.api_key)
        self._ws_client = ws

        def on_tick(t):
            und = getattr(t, "underlying", "") or ""
            if und:  # option tick
                if und in self.tape:
                    self.tape[und].append(t)
            elif t.symbol.upper() in self.tape and t.price:
                self._last_spot[t.symbol.upper()] = float(t.price)

        ws.on("tick", on_tick)
        ws.on("authenticated", lambda: setattr(self, "ws_status", "streaming"))
        ws.on("disconnected", lambda: setattr(self, "ws_status", "reconnecting"))
        ws.on("error", lambda msg: setattr(self, "ws_status", f"error: {msg}"))
        ws.subscribe_options(syms)

        def run():
            try:
                ws.connect(syms)
            except Exception as e:  # thread must never take the kernel down
                self.ws_status = f"stopped: {e}"

        self.ws_status = "connecting"
        self._ws_thread = threading.Thread(target=run, name="lse-stream", daemon=True)
        self._ws_thread.start()

    def stop_stream(self):
        if self._ws_client is not None:
            try:
                self._ws_client.disconnect()
            except Exception:
                pass
        self._ws_client = None
        self._ws_thread = None
        self.ws_status = "off"

    def live_spot(self, symbol):
        return self._last_spot.get(symbol.upper())

    def live_tape(self, symbol) -> pd.DataFrame:
        ticks = list(self.tape.get(symbol.upper(), []))
        if not ticks:
            return pd.DataFrame()
        return pd.DataFrame([{
            "ts": t.timestamp, "ticker": t.symbol, "type": t.right, "strike": t.strike,
            "expiry": t.expiry, "price": t.price, "size": t.volume, "bid": t.bid, "ask": t.ask,
            "premium": t.notional,
        } for t in ticks])

    # -- REST ---------------------------------------------------------------
    def spot(self, symbol) -> float | None:
        s = self.live_spot(symbol)
        if s:
            return s
        try:
            rows = self.client.candles(symbol, "1m", limit=1, order="desc")
            if rows:
                self._last_spot[symbol.upper()] = float(rows[0]["close"])
        except Exception:
            pass
        return self._last_spot.get(symbol.upper())

    def chain(self, symbol) -> pd.DataFrame:
        """Pull the chain in DTE buckets so no single call hits the row cap."""
        spot = self.spot(symbol)
        edges = [(0, 1), (2, 7), (8, 21), (22, 45), (46, 90), (91, 400)]
        buckets = [(lo, min(hi, self.max_dte)) for lo, hi in edges if lo <= self.max_dte]
        rows = []
        for lo, hi in buckets:
            kw = dict(min_dte=lo, max_dte=hi, limit=5000)
            if spot:
                kw["strike"] = (round(spot * (1 - self.strike_band), 2),
                                round(spot * (1 + self.strike_band), 2))
            part = self.client.options(symbol, **kw)
            rows.extend(part)
            if not spot and part:  # learn spot from the first bucket
                ups = [r.get("underlying_price") for r in part if r.get("underlying_price")]
                spot = float(np.median(ups)) if ups else None
        df = normalize_chain(rows, symbol, spot_hint=spot)
        df = df.drop_duplicates("ticker", keep="last")
        if len(df) and np.isfinite(df["spot"].iloc[0]):
            self._last_spot.setdefault(symbol.upper(), float(df["spot"].iloc[0]))
        return df

    def flow(self, symbol) -> pd.DataFrame:
        """Incrementally accumulate today's prints >= flow_min_premium."""
        sym = symbol.upper()
        have = self._flow.get(sym)
        if have is None or have.empty:
            start = session_start_utc().strftime("%Y-%m-%dT%H:%M:%S")
        else:
            start = (have["ts"].max() - pd.Timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S")
        new = self._pull_flow(sym, start)
        if (have is None or have.empty) and new.empty:
            # Market closed / pre-market: show the most recent session instead.
            new = normalize_flow(self.client.options_flow(
                sym, min_premium=self.flow_min_premium, order="desc", limit=5000), sym)
        frames = [f for f in (have, new) if f is not None and not f.empty]
        allf = pd.concat(frames, ignore_index=True) if frames else new
        if not allf.empty:
            allf = (allf.drop_duplicates(["ts", "ticker", "size", "price"])
                        .sort_values("ts").tail(250_000).reset_index(drop=True))
        self._flow[sym] = allf
        return allf

    def _pull_flow(self, sym, start, max_pages=10) -> pd.DataFrame:
        pages, end = [], None
        for _ in range(max_pages):
            rows = self.client.options_flow(sym, min_premium=self.flow_min_premium, start=start,
                                            end=end, order="desc", limit=5000)
            if not rows:
                break
            pages.append(normalize_flow(rows, sym))
            if len(rows) < 5000:
                break
            end = pages[-1]["ts"].min().strftime("%Y-%m-%dT%H:%M:%S")
        return pd.concat(pages, ignore_index=True) if pages else normalize_flow([], sym)

    def intraday(self, symbol) -> pd.DataFrame:
        start = session_start_utc().strftime("%Y-%m-%dT%H:%M:%S")
        bars = _bars(self.client.candles(symbol, "1m", start=start, limit=1000))
        if bars.empty:
            bars = _bars(self.client.candles(symbol, "1m", limit=400, order="desc"))
        if len(bars):
            self._last_spot.setdefault(symbol.upper(), float(bars["close"].iloc[-1]))
        return bars

    def daily(self, symbol) -> pd.DataFrame:
        today = datetime.now(NY).strftime("%Y-%m-%d")
        cached = self._daily.get(symbol.upper())
        if cached and cached[0] == today:
            return cached[1]
        start = (datetime.now(NY) - timedelta(days=200)).strftime("%Y-%m-%d")
        bars = _bars(self.client.candles(symbol, "1d", start=start, limit=400))
        self._daily[symbol.upper()] = (today, bars)
        return bars


# ----------------------------------------------------------------------------
# Demo feed (no key needed) - synthetic but internally consistent
# ----------------------------------------------------------------------------

class DemoFeed:
    """Synthetic SPY/QQQ market so the dashboard runs before a key is pasted.

    Spot follows a random walk, IV has a realistic put skew and term
    structure, prices come from Black-Scholes, prints arrive continuously.
    """

    live = False
    source = "DEMO (synthetic data)"
    ws_status = "off"

    _PARAMS = {"SPY": dict(spot=662.0, iv=0.135, skew=-0.40, step=1.0, rv=0.12),
               "QQQ": dict(spot=596.0, iv=0.175, skew=-0.32, step=1.0, rv=0.16)}

    def __init__(self, seed: int = 7, max_dte: int = 60, flow_min_premium: float = 25_000):
        self.rng = np.random.default_rng(seed)
        self.max_dte = max_dte
        self.flow_min_premium = flow_min_premium
        self._state = {}
        for sym, p in self._PARAMS.items():
            self._init_symbol(sym, p)

    def _init_symbol(self, sym, p):
        now = datetime.now(timezone.utc)
        open_ = session_start_utc(now)
        minutes = int(min(max((now - open_).total_seconds() // 60, 30), 390))
        vol_1m = p["rv"] / np.sqrt(252 * 390)
        path = p["spot"] * np.exp(np.cumsum(self.rng.normal(0, vol_1m, minutes)))
        ts = pd.date_range(end=pd.Timestamp(now).floor("min"), periods=minutes, freq="min")
        intraday = pd.DataFrame({"ts": ts, "open": path, "high": path * 1.0004,
                                 "low": path * 0.9996, "close": path, "volume": 1e5})
        days = pd.bdate_range(end=pd.Timestamp(now).normalize() - pd.Timedelta(days=1), periods=140)
        rets = self.rng.normal(0, p["rv"] / np.sqrt(252), len(days))
        # walk backwards from yesterday's close (~ today's open) so the day change is realistic
        dpath = path[0] * np.exp(self.rng.normal(0, 0.002)) * np.exp(-np.cumsum(rets[::-1]))[::-1]
        daily = pd.DataFrame({"ts": days, "open": dpath, "high": dpath, "low": dpath,
                              "close": dpath, "volume": 5e7})
        self._state[sym] = dict(p=p, intraday=intraday, daily=daily, flow=_empty(FLOW_COLUMNS),
                                last_flow=ts[0], chain_seed=int(self.rng.integers(1e9)))

    def _expiries(self):
        today = datetime.now(NY).date()
        out, d = [], today
        while (d - today).days <= self.max_dte:
            dte = (d - today).days
            if d.weekday() < 5 and (dte <= 10 or d.weekday() == 4):
                out.append(pd.Timestamp(d))
            d += timedelta(days=1)
        return out

    def _iv(self, p, k, T):
        # k = log-moneyness; ATM term structure mildly upward, skew flattens with T.
        atm = p["iv"] * (0.92 + 0.12 * np.sqrt(np.minimum(T, 0.25) / 0.25))
        m = k / np.sqrt(np.maximum(T, 1 / 365))
        return np.clip(atm * (1 + 4 * p["skew"] * m + 3 * m ** 2), 0.05, 1.5)

    def _advance(self, sym):
        st = self._state[sym]
        now = pd.Timestamp(datetime.now(timezone.utc)).floor("min")
        bars = st["intraday"]
        last_ts, last_px = bars["ts"].iloc[-1], bars["close"].iloc[-1]
        vol_1m = st["p"]["rv"] / np.sqrt(252 * 390)
        n = int((now - last_ts).total_seconds() // 60)
        # Also jitter the current bar so the demo visibly ticks every refresh.
        jitter = float(np.exp(self.rng.normal(0, vol_1m * 0.35)))
        bars.loc[bars.index[-1], "close"] = last_px * jitter
        if n > 0:
            px = bars["close"].iloc[-1] * np.exp(np.cumsum(self.rng.normal(0, vol_1m, n)))
            new = pd.DataFrame({"ts": pd.date_range(last_ts + pd.Timedelta(minutes=1), periods=n, freq="min"),
                                "open": px, "high": px * 1.0004, "low": px * 0.9996, "close": px, "volume": 1e5})
            bars = pd.concat([bars, new], ignore_index=True).tail(390)
        st["intraday"] = bars.reset_index(drop=True)

    def spot(self, symbol):
        return float(self._state[symbol.upper()]["intraday"]["close"].iloc[-1])

    def live_spot(self, symbol):
        return None

    def live_tape(self, symbol):
        return pd.DataFrame()

    def start_stream(self, symbols):
        pass

    def stop_stream(self):
        pass

    def chain(self, symbol) -> pd.DataFrame:
        from .analytics import bs_price_greeks
        sym = symbol.upper()
        self._advance(sym)
        st, p = self._state[sym], self._state[sym]["p"]
        S = self.spot(sym)
        rng = np.random.default_rng(st["chain_seed"])
        rows = []
        strikes = np.arange(np.floor(S * 0.88), np.ceil(S * 1.12) + 1, p["step"])
        for exp in self._expiries():
            dte, T = _time_to_expiry(pd.Series([exp]))
            T = float(T.iloc[0])
            k = np.log(strikes / S)
            iv = self._iv(p, k, T)
            width = 0.02 + 0.2 * np.sqrt(T)
            round_bonus = np.where(strikes % 5 == 0, 2.5, 1.0) * np.where(strikes % 10 == 0, 1.6, 1.0)
            d = max(float(dte.iloc[0]), 0.05)
            dte_w = 1 / (0.4 + np.sqrt(d))
            for typ in ("call", "put"):
                is_call = typ == "call"
                g = bs_price_greeks(S, strikes, T, iv, np.full(len(strikes), is_call))
                # calls cluster above spot, puts below (overwriting calls, hedging puts)
                center = 0.012 if is_call else -0.02
                near = np.exp(-((k - center * np.sqrt(d / 7 + 0.3)) / width) ** 2)
                vol = np.round(6_000 * near * round_bonus * dte_w * (1.2 if not is_call else 1.0)
                               * rng.lognormal(0, 0.35, len(strikes)))
                oi = np.round(vol * rng.uniform(0.6, 1.6, len(strikes)) * (0.4 + d / 2))
                hot = rng.random(len(strikes)) < 0.006   # the odd unusual-activity spike
                vol = np.where(hot, vol * 12 + 2_000, vol)
                spread = np.maximum(0.01, g["price"] * 0.015)
                for i, K in enumerate(strikes):
                    px = max(float(g["price"][i]), 0.01)
                    ticker = f"{sym}{exp:%y%m%d}{'C' if is_call else 'P'}{int(round(K * 1000)):08d}"
                    rows.append(dict(ticker=ticker, type=typ, strike=K, expiry=exp.strftime("%Y-%m-%d"),
                                     last_price=px, bid=max(px - spread[i] / 2, 0.0), ask=px + spread[i] / 2,
                                     iv=iv[i], delta=g["delta"][i], gamma=g["gamma"][i],
                                     theta=g["theta"][i], vega=g["vega"][i],
                                     volume_today=vol[i], premium_today=vol[i] * px * 100,
                                     open_interest=oi[i], underlying_price=S))
        return normalize_chain(rows, sym)

    def flow(self, symbol) -> pd.DataFrame:
        sym = symbol.upper()
        st = self._state[sym]
        now = pd.Timestamp(datetime.now(timezone.utc))
        start = st["last_flow"]
        mins = max((now - start).total_seconds() / 60, 0.2)
        n = min(int(self.rng.poisson(9 * mins)), 4000)
        if n:
            chain = st.get("chain_cache")
            if chain is None or self.rng.random() < 0.1:
                chain = self.chain(sym)
                st["chain_cache"] = chain
            chain = chain[chain["dte"] <= 45]
            w = (chain["volume"] + 1).to_numpy()
            idx = self.rng.choice(len(chain), size=n, p=w / w.sum())
            picks = chain.iloc[idx].reset_index(drop=True)
            sizes = np.round(self.rng.lognormal(4.3, 1.1, n)) + 1
            buy = self.rng.random(n) < np.where(picks["type"] == "call", 0.56, 0.5)
            price = np.where(buy, picks["ask"], picks["bid"]).astype(float)
            price = np.maximum(price, 0.01)
            ts = start + (now - start) * np.sort(self.rng.random(n))
            prints = pd.DataFrame(dict(ts=ts, ticker=picks["ticker"], type=picks["type"],
                                       strike=picks["strike"], expiry=picks["expiry"], price=price,
                                       size=sizes, premium=price * sizes * 100, iv=picks["iv"],
                                       delta=picks["delta"], gamma=picks["gamma"], bid=picks["bid"],
                                       ask=picks["ask"], underlying_price=self.spot(sym)))
            new = normalize_flow(prints[prints["premium"] >= self.flow_min_premium], sym)
            if not new.empty:
                st["flow"] = new if st["flow"].empty else pd.concat([st["flow"], new], ignore_index=True)
        st["last_flow"] = now
        return st["flow"]

    def intraday(self, symbol):
        self._advance(symbol.upper())
        return self._state[symbol.upper()]["intraday"].copy()

    def daily(self, symbol):
        return self._state[symbol.upper()]["daily"].copy()


def make_feed(api_key: str | None, **kwargs):
    """Live LSE feed when a real key is given, otherwise the demo feed."""
    if not has_real_key(api_key):
        return DemoFeed(max_dte=kwargs.get("max_dte", 60),
                        flow_min_premium=kwargs.get("flow_min_premium", 25_000))
    return LSEFeed(api_key.strip(), **kwargs)


def has_real_key(api_key: str | None) -> bool:
    key = (api_key or "").strip()
    return bool(key) and "PASTE" not in key.upper()
