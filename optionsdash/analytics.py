"""Quant analytics built on top of the normalized chain and flow frames.

Conventions
-----------
* Dealer exposure (GEX / vanna / charm) uses the standard street convention:
  customers are net long puts and net short calls, so dealers are long call
  gamma and short put gamma. Calls count +, puts count -. This is an
  approximation of true dealer positioning, which no public feed reveals.
* Positioning weight is open interest when the feed provides it, otherwise
  today's traded volume (clearly labelled on every chart).
* All vol numbers are annualized decimals internally and shown in vol points.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import norm

RATE = 0.04    # risk-free rate used when the model re-prices greeks
DIV = {"SPY": 0.012, "QQQ": 0.006}


# ----------------------------------------------------------------------------
# Black-Scholes
# ----------------------------------------------------------------------------

def bs_price_greeks(S, K, T, sigma, is_call, r: float = RATE, q: float = 0.0) -> dict:
    """Vectorized Black-Scholes price and greeks.

    Returns delta, gamma, theta (per day), vega (per vol point), vanna
    (d delta / d vol, per 1.00 vol) and charm (d delta / d time, per day).
    """
    S, K, T, sigma = (np.asarray(x, dtype=float) for x in (S, K, T, sigma))
    is_call = np.asarray(is_call, dtype=bool)
    T = np.maximum(T, 1e-6)
    sigma = np.maximum(sigma, 1e-4)
    sqT = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    pdf = norm.pdf(d1)
    dq, dr = np.exp(-q * T), np.exp(-r * T)
    Nd1, Nd2 = norm.cdf(d1), norm.cdf(d2)
    call = S * dq * Nd1 - K * dr * Nd2
    put = K * dr * norm.cdf(-d2) - S * dq * norm.cdf(-d1)
    delta = np.where(is_call, dq * Nd1, dq * (Nd1 - 1))
    gamma = dq * pdf / (S * sigma * sqT)
    vega = S * dq * pdf * sqT / 100
    theta_c = -S * dq * pdf * sigma / (2 * sqT) - r * K * dr * Nd2 + q * S * dq * Nd1
    theta_p = -S * dq * pdf * sigma / (2 * sqT) + r * K * dr * norm.cdf(-d2) - q * S * dq * norm.cdf(-d1)
    vanna = -dq * pdf * d2 / sigma
    common = dq * pdf * (2 * (r - q) * T - d2 * sigma * sqT) / (2 * T * sigma * sqT)
    charm = np.where(is_call, q * dq * Nd1 - common, -q * dq * norm.cdf(-d1) - common)
    return dict(price=np.where(is_call, call, put), delta=delta, gamma=gamma,
                theta=np.where(is_call, theta_c, theta_p) / 365, vega=vega,
                vanna=vanna, charm=charm / 365)


# ----------------------------------------------------------------------------
# Chain preparation
# ----------------------------------------------------------------------------

def prepare_chain(chain: pd.DataFrame, spot: float, symbol: str) -> pd.DataFrame:
    """Add model greeks, positioning weight and dealer sign to a chain."""
    df = chain.copy()
    if df.empty:
        return df
    q = DIV.get(symbol.upper(), 0.0)
    df = df[df["iv"].notna() & (df["strike"] > 0)].copy()
    is_call = (df["type"] == "call").to_numpy()
    g = bs_price_greeks(spot, df["strike"], df["T"], df["iv"], is_call, q=q)
    # Prefer the feed's own delta/gamma, fall back to the model.
    df["delta"] = df["delta"].fillna(pd.Series(g["delta"], index=df.index))
    df["gamma"] = df["gamma"].fillna(pd.Series(g["gamma"], index=df.index))
    df["vanna"] = g["vanna"]
    df["charm"] = g["charm"]
    df["model_price"] = g["price"]
    has_oi = df["oi"].fillna(0).sum() > 0
    df["weight"] = df["oi"].fillna(0) if has_oi else df["volume"].fillna(0)
    df.attrs["weight_label"] = "open interest" if has_oi else "today's volume (no OI in feed)"
    df["sign"] = np.where(is_call, 1.0, -1.0)
    df["moneyness"] = df["strike"] / spot - 1
    return df


def weight_label(df: pd.DataFrame) -> str:
    return df.attrs.get("weight_label", "open interest")


# ----------------------------------------------------------------------------
# Dealer positioning
# ----------------------------------------------------------------------------

def exposure_by_strike(df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Dealer gamma ($ per 1% move), vanna ($ delta per vol pt), charm ($ delta per day)."""
    if df.empty:
        return pd.DataFrame(columns=["strike", "call_gex", "put_gex", "gex", "vanna", "charm",
                                     "call_w", "put_w"])
    mult = df["weight"] * 100
    e = pd.DataFrame({
        "strike": df["strike"],
        "gex": df["sign"] * df["gamma"] * mult * spot ** 2 * 0.01,
        "vanna": df["sign"] * df["vanna"] * mult * spot * 0.01,
        "charm": df["sign"] * df["charm"] * mult * spot,
        "is_call": df["type"] == "call",
        "w": df["weight"],
    })
    e["call_gex"] = np.where(e["is_call"], e["gex"], 0.0)
    e["put_gex"] = np.where(~e["is_call"], e["gex"], 0.0)
    e["call_w"] = np.where(e["is_call"], e["w"], 0.0)
    e["put_w"] = np.where(~e["is_call"], e["w"], 0.0)
    out = e.groupby("strike")[["call_gex", "put_gex", "gex", "vanna", "charm", "call_w", "put_w"]].sum()
    return out.reset_index()


def gamma_profile(df: pd.DataFrame, spot: float, symbol: str, span: float = 0.08, n: int = 121):
    """Total dealer gamma re-priced at hypothetical spot levels.

    Returns (grid DataFrame[spot, gex], flip level or None).
    """
    if df.empty:
        return pd.DataFrame(columns=["spot", "gex"]), None
    q = DIV.get(symbol.upper(), 0.0)
    grid = np.linspace(spot * (1 - span), spot * (1 + span), n)
    K = df["strike"].to_numpy()[None, :]
    T = df["T"].to_numpy()[None, :]
    iv = df["iv"].to_numpy()[None, :]
    w = (df["weight"] * df["sign"]).to_numpy()[None, :]
    S = grid[:, None]
    g = bs_price_greeks(S, K, T, iv, True, q=q)["gamma"]
    gex = (g * w * 100 * S ** 2 * 0.01).sum(axis=1)
    prof = pd.DataFrame({"spot": grid, "gex": gex})
    flip = None
    sgn = np.sign(gex)
    cross = np.where(np.diff(sgn) != 0)[0]
    if len(cross):
        levels = []
        for i in cross:
            x0, x1, y0, y1 = grid[i], grid[i + 1], gex[i], gex[i + 1]
            levels.append(x0 - y0 * (x1 - x0) / (y1 - y0))
        flip = float(min(levels, key=lambda x: abs(x - spot)))
    return prof, flip


def max_pain(df: pd.DataFrame, expiry) -> float | None:
    sub = df[df["expiry"] == expiry]
    if sub.empty or sub["weight"].sum() <= 0:
        return None
    strikes = np.sort(sub["strike"].unique())
    c = sub[sub["type"] == "call"]
    p = sub[sub["type"] == "put"]
    pain = [(c["weight"] * np.maximum(P - c["strike"], 0)).sum()
            + (p["weight"] * np.maximum(p["strike"] - P, 0)).sum() for P in strikes]
    return float(strikes[int(np.argmin(pain))])


# ----------------------------------------------------------------------------
# Volatility
# ----------------------------------------------------------------------------

def smile(df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """OTM implied vol per expiry: puts below spot, calls at/above spot."""
    otm = df[((df["type"] == "put") & (df["strike"] < spot)) |
             ((df["type"] == "call") & (df["strike"] >= spot))]
    otm = otm[otm["iv"].between(0.01, 3)]
    return otm[["expiry", "dte", "T", "strike", "moneyness", "iv", "delta", "type"]].sort_values(
        ["expiry", "strike"])


def term_structure(df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Per expiry: ATM IV, 25-delta risk reversal & butterfly, expected move."""
    rows = []
    sm = smile(df, spot)
    for exp, g in df.groupby("expiry"):
        s = sm[sm["expiry"] == exp]
        if len(s) < 3:
            continue
        atm = float(np.interp(spot, s["strike"], s["iv"]))
        T = float(g["T"].iloc[0])
        dte = float(g["dte"].iloc[0])
        calls = g[(g["type"] == "call") & g["iv"].between(0.01, 3)].sort_values("delta")
        puts = g[(g["type"] == "put") & g["iv"].between(0.01, 3)].sort_values("delta")
        c25 = p25 = np.nan
        if len(calls) >= 3 and calls["delta"].min() < 0.25 < calls["delta"].max():
            c25 = float(np.interp(0.25, calls["delta"], calls["iv"]))
        if len(puts) >= 3 and puts["delta"].min() < -0.25 < puts["delta"].max():
            p25 = float(np.interp(-0.25, puts["delta"], puts["iv"]))
        # ATM straddle price = the market's own expected move.
        k_atm = g.loc[(g["strike"] - spot).abs().idxmin(), "strike"]
        straddle = g[g["strike"] == k_atm]["mid"].sum() if len(g[g["strike"] == k_atm]) == 2 else np.nan
        rows.append(dict(expiry=exp, dte=dte, T=T, atm_iv=atm, c25=c25, p25=p25,
                         rr25=c25 - p25, fly25=(c25 + p25) / 2 - atm,
                         em_iv=spot * atm * np.sqrt(T), em_straddle=straddle,
                         volume=g["volume"].sum()))
    return pd.DataFrame(rows).sort_values("dte").reset_index(drop=True) if rows else pd.DataFrame(
        columns=["expiry", "dte", "T", "atm_iv", "c25", "p25", "rr25", "fly25", "em_iv", "em_straddle", "volume"])


def constant_maturity(ts: pd.DataFrame, days: float, col: str = "atm_iv") -> float:
    """Interpolate a term-structure column at a fixed maturity (variance-time for IV)."""
    t = ts.dropna(subset=[col])
    if t.empty:
        return np.nan
    if col == "atm_iv":
        var = t[col] ** 2 * t["T"]
        v = np.interp(days / 365, t["T"], var)
        return float(np.sqrt(max(v, 0) / (days / 365)))
    return float(np.interp(days, t["dte"], t[col]))


def surface(df: pd.DataFrame, spot: float, bins=np.arange(-0.10, 0.1001, 0.01)) -> pd.DataFrame:
    """IV grid: rows = expiry, cols = moneyness bucket (OTM options)."""
    s = smile(df, spot)
    if s.empty:
        return pd.DataFrame()
    out = {}
    for exp, g in s.groupby("expiry"):
        g = g.sort_values("moneyness")
        vals = np.interp(bins, g["moneyness"], g["iv"], left=np.nan, right=np.nan)
        out[(exp, float(g["dte"].iloc[0]))] = vals
    grid = pd.DataFrame(out, index=bins).T
    grid.index.names = ["expiry", "dte"]
    return grid


def realized_vol(daily: pd.DataFrame, window: int = 20) -> float:
    if daily is None or len(daily) < window + 1:
        return np.nan
    r = np.log(daily["close"]).diff().dropna().tail(window)
    return float(r.std() * np.sqrt(252))


def intraday_realized_vol(bars: pd.DataFrame) -> float:
    if bars is None or len(bars) < 20:
        return np.nan
    r = np.log(bars["close"]).diff().dropna()
    return float(r.std() * np.sqrt(252 * 390))


# ----------------------------------------------------------------------------
# Flow
# ----------------------------------------------------------------------------

def classify_flow(flow: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Infer aggressor side for each print and derive directional metrics.

    side: +1 customer bought, -1 customer sold, 0 unknown.
    Quote rule when bid/ask exist (at/above ask = buy, at/below bid = sell,
    otherwise nearer side); tick test against the previous print on the same
    contract when they do not.
    """
    f = flow.copy()
    for c in ("price", "size", "premium", "bid", "ask", "delta", "iv", "dte", "strike"):
        if c in f.columns:
            f[c] = pd.to_numeric(f[c], errors="coerce")
    if f.empty:
        for c in ("side", "bull_premium", "delta_dollars", "method"):
            f[c] = []
        return f
    side = pd.Series(0.0, index=f.index)
    quoted = f["bid"].gt(0) & f["ask"].gt(0)
    mid = (f["bid"] + f["ask"]) / 2
    side[quoted & (f["price"] >= mid)] = 1.0
    side[quoted & (f["price"] < mid)] = -1.0
    side[quoted & (f["price"] == mid)] = 0.0
    tick = np.sign(f.groupby("ticker")["price"].diff()).fillna(0)
    side[~quoted] = tick[~quoted]
    f["side"] = side
    f["method"] = np.where(quoted, "quote", "tick")
    call = f["type"] == "call"
    f["bull_premium"] = f["premium"] * f["side"] * np.where(call, 1, -1)
    delta = f["delta"].fillna(pd.Series(np.where(call, 0.5, -0.5), index=f.index))
    f["delta_dollars"] = f["side"] * delta * f["size"] * 100 * spot
    return f


def flow_timeline(f: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    if f.empty:
        return pd.DataFrame(columns=["ts", "call_prem", "put_prem", "net_bull", "cum_call", "cum_put", "cum_net"])
    g = f.set_index("ts")
    t = pd.DataFrame({
        "call_prem": g["premium"].where(g["type"] == "call", 0).resample(freq).sum(),
        "put_prem": g["premium"].where(g["type"] == "put", 0).resample(freq).sum(),
        "net_bull": g["bull_premium"].resample(freq).sum(),
    }).fillna(0)
    t["cum_call"] = t["call_prem"].cumsum()
    t["cum_put"] = t["put_prem"].cumsum()
    t["cum_net"] = t["net_bull"].cumsum()
    return t.reset_index()


DTE_BUCKETS = [(-1, 0.99, "0DTE"), (0.99, 7.99, "1-7d"), (7.99, 30.99, "8-30d"), (30.99, 1e9, "31d+")]


def flow_by_dte(f: pd.DataFrame) -> pd.DataFrame:
    labels = [b[2] for b in DTE_BUCKETS]
    if f.empty:
        return pd.DataFrame({"bucket": labels, "call": 0.0, "put": 0.0})
    b = pd.cut(f["dte"], [DTE_BUCKETS[0][0]] + [x[1] for x in DTE_BUCKETS], labels=labels)
    t = f.assign(bucket=b).pivot_table(index="bucket", columns="type", values="premium",
                                       aggfunc="sum", observed=False).reindex(labels).fillna(0)
    return pd.DataFrame({"bucket": labels, "call": t.get("call", 0.0), "put": t.get("put", 0.0)}).reset_index(drop=True)


def flow_by_strike(f: pd.DataFrame, spot: float, band: float = 0.06) -> pd.DataFrame:
    if f.empty:
        return pd.DataFrame(columns=["strike", "call", "put"])
    s = f[(f["strike"] / spot - 1).abs() <= band]
    t = s.pivot_table(index="strike", columns="type", values="premium", aggfunc="sum").fillna(0)
    return pd.DataFrame({"strike": t.index, "call": t.get("call", 0.0), "put": t.get("put", 0.0)}).reset_index(drop=True)


def unusual_activity(df: pd.DataFrame, top: int = 12) -> pd.DataFrame:
    """Contracts trading far above their open interest (or chain-relative volume)."""
    if df.empty:
        return df
    d = df[df["volume"] > 0].copy()
    if d["oi"].fillna(0).sum() > 0:
        d["score"] = d["volume"] / d["oi"].clip(lower=50)
        d["basis"] = "vol/OI"
    else:
        d["score"] = d["premium_today"] / max(d["premium_today"].median(), 1)
        d["basis"] = "prem/median"
    d = d[(d["premium_today"] >= 50_000) & d["delta"].abs().between(0.03, 0.92)]
    return d.sort_values("score", ascending=False).head(top)


# ----------------------------------------------------------------------------
# One-shot snapshot of everything the dashboard needs
# ----------------------------------------------------------------------------

@dataclass
class Snapshot:
    symbol: str
    spot: float
    prev_close: float
    chain: pd.DataFrame
    exposure: pd.DataFrame
    profile: pd.DataFrame
    flip: float | None
    term: pd.DataFrame
    smile: pd.DataFrame
    surface: pd.DataFrame
    flow: pd.DataFrame
    timeline: pd.DataFrame
    by_dte: pd.DataFrame
    by_strike: pd.DataFrame
    unusual: pd.DataFrame
    intraday: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    insights: list = field(default_factory=list)


def build_snapshot(symbol, chain, flow, intraday, daily, live_spot=None) -> Snapshot:
    spot = live_spot or (float(intraday["close"].iloc[-1]) if len(intraday) else float(chain["spot"].iloc[0]))
    prev_close = float(daily["close"].iloc[-1]) if len(daily) else np.nan
    if len(daily) and pd.Timestamp(daily["ts"].iloc[-1]).date() >= pd.Timestamp.now(tz="UTC").date() and len(daily) > 1:
        prev_close = float(daily["close"].iloc[-2])

    c = prepare_chain(chain, spot, symbol)
    expo = exposure_by_strike(c, spot)
    prof, flip = gamma_profile(c, spot, symbol)
    ts = term_structure(c, spot)
    sm = smile(c, spot)
    surf = surface(c, spot)
    f = classify_flow(flow, spot)
    tl = flow_timeline(f)

    m = {"spot": spot, "prev_close": prev_close, "chg": spot / prev_close - 1 if prev_close else np.nan}
    m["weight_label"] = weight_label(c)
    m["net_gex"] = float(expo["gex"].sum()) if len(expo) else np.nan
    m["flip"] = flip
    near = expo[(expo["strike"] / spot - 1).abs() <= 0.10]
    m["call_wall"] = float(near.loc[near["call_gex"].idxmax(), "strike"]) if len(near) and near["call_gex"].max() > 0 else np.nan
    m["put_wall"] = float(near.loc[near["put_gex"].idxmin(), "strike"]) if len(near) and near["put_gex"].min() < 0 else np.nan
    m["net_vanna"] = float(expo["vanna"].sum()) if len(expo) else np.nan
    m["net_charm"] = float(expo["charm"].sum()) if len(expo) else np.nan
    m["atm_iv_30"] = constant_maturity(ts, 30)
    m["atm_iv_7"] = constant_maturity(ts, 7)
    m["rr25_30"] = constant_maturity(ts, 30, "rr25")
    m["fly25_30"] = constant_maturity(ts, 30, "fly25")
    m["rv20"] = realized_vol(daily, 20)
    m["rv_intraday"] = intraday_realized_vol(intraday)
    m["vrp"] = m["atm_iv_30"] - m["rv20"]
    front = ts.iloc[0] if len(ts) else None
    m["front_expiry"] = front["expiry"] if front is not None else None
    m["em_front"] = (front["em_straddle"] if front is not None and np.isfinite(front["em_straddle"])
                     else (front["em_iv"] if front is not None else np.nan))
    m["max_pain"] = max_pain(c, front["expiry"]) if front is not None else None
    cv = c.loc[c["type"] == "call", "volume"].sum()
    pv = c.loc[c["type"] == "put", "volume"].sum()
    cp = c.loc[c["type"] == "call", "premium_today"].sum()
    pp = c.loc[c["type"] == "put", "premium_today"].sum()
    m["pc_volume"] = pv / cv if cv else np.nan
    m["pc_premium"] = pp / cp if cp else np.nan
    m["call_premium"], m["put_premium"] = cp, pp
    m["net_flow"] = float(f["bull_premium"].sum()) if len(f) else 0.0
    m["net_delta_flow"] = float(f["delta_dollars"].sum()) if len(f) else 0.0
    m["flow_known"] = float((f["side"] != 0).mean()) if len(f) else np.nan
    m["prints"] = len(f)
    m["contracts"] = len(c)

    snap = Snapshot(symbol, spot, prev_close, c, expo, prof, flip, ts, sm, surf, f, tl,
                    flow_by_dte(f), flow_by_strike(f, spot), unusual_activity(c), intraday, m)
    snap.insights = insights(snap)
    return snap


# ----------------------------------------------------------------------------
# Plain-English read of the tape
# ----------------------------------------------------------------------------

def _money(x):
    if x is None or not np.isfinite(x):
        return "n/a"
    a = abs(x)
    s = "-" if x < 0 else ""
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.0f}K"
    return f"{s}${a:.0f}"


def insights(s: Snapshot) -> list[tuple[str, str]]:
    """Return (tag, sentence) pairs; tag is one of bull / bear / neutral / vol."""
    m, out = s.metrics, []
    spot = s.spot
    gex, flip = m["net_gex"], m["flip"]
    if np.isfinite(gex):
        if gex > 0:
            txt = (f"Positive dealer gamma ({_money(gex)} per 1% move). Dealer hedging sells rallies and "
                   f"buys dips, so moves tend to mean-revert and realized vol stays compressed.")
        else:
            txt = (f"Negative dealer gamma ({_money(gex)} per 1% move). Dealer hedging chases price, "
                   f"so moves can extend and intraday ranges widen.")
        if flip:
            side = "above" if spot > flip else "below"
            txt += f" Spot is {abs(spot / flip - 1) * 100:.2f}% {side} the gamma flip at {flip:.1f}."
        out.append(("neutral" if gex > 0 else "bear", txt))
    cw, pw = m.get("call_wall", np.nan), m.get("put_wall", np.nan)
    if np.isfinite(cw) and np.isfinite(pw):
        if cw == pw:
            out.append(("neutral", f"Call and put gamma both peak at {cw:.0f}: a strong pin level "
                                   f"({(cw / spot - 1) * 100:+.2f}% from spot)."))
        else:
            out.append(("neutral", f"Largest call gamma at {cw:.0f} (resistance / pin magnet), "
                                   f"largest put gamma at {pw:.0f} (support). "
                                   f"Range between them: {abs(cw - pw) / spot * 100:.1f}% of spot."))
    em = m.get("em_front")
    if em and np.isfinite(em) and m.get("front_expiry") is not None:
        out.append(("vol", f"Front expiry ({pd.Timestamp(m['front_expiry']):%b %d}) prices a "
                           f"±{em:.2f} move (±{em / spot * 100:.2f}%): {spot - em:.1f} to {spot + em:.1f}."))
    ch, va = m.get("net_charm", np.nan), m.get("net_vanna", np.nan)
    if np.isfinite(ch):
        way = "buy" if ch < 0 else "sell"
        out.append(("bull" if ch < 0 else "bear",
                    f"Charm: with time decay alone dealers must {way} about {_money(abs(ch))} of delta per day "
                    f"to stay hedged, a {'tailwind' if ch < 0 else 'headwind'} into the close and expiry."))
    if np.isfinite(va):
        out.append(("neutral", f"Vanna: a 1 vol-pt drop in IV makes dealers "
                               f"{'buy' if va > 0 else 'sell'} about {_money(abs(va))} of delta"
                               f" ({'vol crush supports price' if va > 0 else 'falling IV weighs on price'})."))
    iv30, rv = m["atm_iv_30"], m["rv20"]
    if np.isfinite(iv30) and np.isfinite(rv):
        vrp = (iv30 - rv) * 100
        tone = "rich (premium sellers paid)" if vrp > 2 else ("cheap (options under-price realized moves)" if vrp < -1 else "fair")
        out.append(("vol", f"30d ATM IV {iv30 * 100:.1f} vs 20d realized {rv * 100:.1f}: implied is {tone}, "
                           f"spread {vrp:+.1f} vol pts."))
    iv7 = m["atm_iv_7"]
    if np.isfinite(iv7) and np.isfinite(iv30):
        if iv7 > iv30 + 0.01:
            out.append(("bear", f"Term structure inverted: 7d IV {iv7 * 100:.1f} above 30d {iv30 * 100:.1f}. "
                                f"The market is paying up for near-term protection (event or stress)."))
        else:
            out.append(("neutral", f"Term structure in contango (7d {iv7 * 100:.1f} vs 30d {iv30 * 100:.1f}), "
                                   f"the normal calm-market shape."))
    rr = m["rr25_30"]
    if np.isfinite(rr):
        out.append(("bear" if rr < -0.05 else "neutral",
                    f"30d 25-delta risk reversal {rr * 100:+.1f} vol pts: puts trade "
                    f"{abs(rr) * 100:.1f} pts over equidistant calls"
                    + (" (steep downside hedging demand)." if rr < -0.05 else ".")))
    if m["prints"]:
        tag = "bull" if m["net_flow"] > 0 else "bear"
        out.append((tag, f"Directional flow: net {_money(m['net_flow'])} bullish premium and "
                         f"{_money(m['net_delta_flow'])} customer delta across {m['prints']:,} large prints "
                         f"(side inferred on {m['flow_known'] * 100:.0f}% of them)."))
    pcv = m["pc_volume"]
    if np.isfinite(pcv):
        out.append(("bear" if pcv > 1.3 else ("bull" if pcv < 0.8 else "neutral"),
                    f"Put/call volume {pcv:.2f}, put/call premium {m['pc_premium']:.2f}."))
    return out
