# Options-flow-dashboard

A live SPY / QQQ options dashboard in Jupyter, built on the
[London Strategic Edge](https://londonstrategicedge.com) options feed. It goes beyond showing IV and delta: it turns the chain and the tape into
dealer positioning, volatility structure and flow signals, and writes a plain-English read of the market.

## Where to paste your API key

Open **`Options_Dashboard.ipynb`** and paste the key into the first code cell:

```python
LSE_API_KEY = "PASTE_YOUR_LSE_API_KEY_HERE"
```

To keep the key out of the notebook (and out of git), set it in the environment instead. The notebook picks it up automatically:

```bash
export LSE_API_KEY=lse_live_xxxxxxxxxxxx
```

With no key, the dashboard runs on **synthetic demo data**, so you can check that everything works before you connect.

## Run it

```bash
pip install -r requirements.txt
jupyter lab Options_Dashboard.ipynb      # then Run → Run All Cells
```

The dashboard renders inside the notebook and refreshes on its own: every 15s for flow, price and KPIs, and every 60s for the full chain.
There's a SPY/QQQ toggle, a pause button and a refresh-rate picker. When the key is live, a WebSocket thread also streams real-time
spot and a live option tape.

## What's on it

| Section | Contents |
|---|---|
| **KPI tiles** | Spot and day change · net dealer gamma ($ per 1% move) · gamma flip · call wall / put wall · max pain · 30d ATM IV vs 20d realized (VRP) · front-expiry expected move · 30d 25Δ risk reversal & butterfly · put/call ratios · net directional premium and customer delta |
| **Market read** | Auto-generated interpretation: gamma regime, pin levels, expected range, charm/vanna hedging pressure, vol richness, term-structure shape, skew, flow bias |
| **Price & Levels** | Intraday price with the call wall, put wall, gamma flip, max pain and expected-move band · expected-move cone across expiries |
| **Dealer Positioning** | GEX by strike (calls vs puts) · net GEX re-priced across ±8% of spot (finds the flip) · open interest by strike · vanna and charm exposure by strike |
| **Volatility** | ATM term structure vs realized · smiles for 0/7/30/60-day expiries · 25Δ RR and butterfly term structure · IV surface heatmap · per-expiry summary table |
| **Options Flow** | Cumulative call vs put premium · net directional premium per 5 min · premium by strike and by expiry bucket · largest prints (side inferred) · unusual activity (volume ≫ OI) |

`dash.save_html("options_dashboard.html")` writes a shareable snapshot of every chart and table for both symbols.
`dash.snapshots["SPY"]` exposes the underlying DataFrames (chain with model greeks, exposures, term structure, classified flow)
for your own research.

## Layout

```
Options_Dashboard.ipynb   main entry point (API key cell, launch, methodology notes)
optionsdash/
  feed.py        LSE feed (REST chain/flow/candles + WebSocket) and the offline demo feed
  analytics.py   Black-Scholes greeks, GEX/vanna/charm, gamma flip, max pain, term structure,
                 skew, expected move, realized vol, flow side classification, market read
  charts.py      Plotly figure builders and HTML tables/KPI tiles
  dashboard.py   ipywidgets live dashboard, refresh loop, static render, HTML export
  theme.py       dark terminal theme (colorblind-checked palette)
```

## Methodology notes

* **Dealer exposure** follows the usual street convention: dealers are long call gamma and short put gamma. It estimates
  positioning. It is not a view of actual dealer books.
* **Positioning weight** is open interest when the feed provides it. Otherwise it falls back to today's volume, and the charts say so.
* **Flow side** uses the quote rule when prints carry bid/ask, otherwise a tick test. The share of prints with a known side is
  shown in the market read.
* **Data volume:** the chain is pulled in DTE buckets within ±12% of spot. Flow is pulled incrementally, and only prints
  ≥ `FLOW_MIN_PREMIUM` are kept. If you are on a metered plan, raise `CHAIN_REFRESH_SECONDS` to cut data usage.
