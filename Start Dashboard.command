#!/bin/sh
# macOS / Linux: double-click (macOS) or run ./"Start Dashboard.command"
cd "$(dirname "$0")" || exit 1
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
echo "Checking libraries (the first run can take a few minutes)..."
"$PY" -m pip install -q --disable-pip-version-check -r requirements.txt || { echo "Installing the libraries failed."; exit 1; }
echo "Starting the dashboard at http://127.0.0.1:8050 (Ctrl+C to stop)"
exec "$PY" run_dashboard.py "$@"
