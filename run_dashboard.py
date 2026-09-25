"""Options Quant Dashboard as a web app: runs in your browser, no notebook needed.

Start it by double-clicking "Start Dashboard.bat" (Windows), or from a terminal:
    python run_dashboard.py
It opens http://127.0.0.1:8050 in your browser. Press Ctrl+C in the terminal to stop.
"""

# ▼▼▼ PASTE YOUR LONDON STRATEGIC EDGE API KEY BETWEEN THE QUOTES ▼▼▼
LSE_API_KEY = "PASTE_YOUR_LSE_API_KEY_HERE"
# ▲▲▲ (keys look like  lse_live_xxxxxxxxxxxx ) ▲▲▲

# Optional settings
SYMBOLS = ["SPY", "QQQ"]
REFRESH_SECONDS = 15          # how often flow / price / headline numbers update
CHAIN_REFRESH_SECONDS = 60    # how often the full option chain is re-pulled (heavier call)
MAX_DTE = 60                  # furthest expiry pulled into the chain (days)
FLOW_MIN_PREMIUM = 25_000     # only prints >= this $ premium feed the flow panels
USE_WEBSOCKET = True          # real-time spot + live option tape over WebSocket
PORT = 8050                   # the page is served at http://127.0.0.1:PORT

if __name__ == "__main__":
    import argparse
    import os

    from optionsdash.webapp import run

    ap = argparse.ArgumentParser(description="Run the options dashboard in your browser.")
    ap.add_argument("--demo", action="store_true", help="use synthetic demo data even if a key is set")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab automatically")
    args = ap.parse_args()

    key = None if args.demo else (os.environ.get("LSE_API_KEY") or LSE_API_KEY)
    run(key, symbols=SYMBOLS, refresh_seconds=REFRESH_SECONDS, chain_refresh_seconds=CHAIN_REFRESH_SECONDS,
        max_dte=MAX_DTE, flow_min_premium=FLOW_MIN_PREMIUM, use_websocket=USE_WEBSOCKET,
        port=args.port, open_browser=not args.no_browser)
