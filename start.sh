#!/usr/bin/env bash
# One command to run the app locally: installs dependencies on first run, then
# starts the backend (port 8000) and the frontend (port 5173). Ctrl+C stops both.
#
#   ./start.sh            # uses ANTHROPIC_API_KEY from backend/.env if present, else the labelled stub
#   PYTHON=/path/to/python3.12 ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for p in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$p" ] && command -v "$p" >/dev/null 2>&1 || continue
    if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then echo "$p"; return; fi
  done
  echo "Python 3.10+ is required (python.org, or 'brew install python@3.12')." >&2; exit 1
}

command -v npm >/dev/null || { echo "Node 20+ is required (nodejs.org)." >&2; exit 1; }

if [ ! -x backend/.venv/bin/python ]; then
  echo "Setting up the backend (first run only)..."
  "$(find_python)" -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/node_modules ]; then
  echo "Setting up the frontend (first run only)..."
  (cd frontend && npm install --silent)
fi

(cd backend && .venv/bin/uvicorn app.main:app --port 8000) &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT

for _ in $(seq 1 30); do curl -s localhost:8000/health >/dev/null && break; sleep 0.5; done
echo "Backend: $(curl -s localhost:8000/health)"
echo "Open http://localhost:5173 in your browser. Ctrl+C to stop."
cd frontend && npm run dev -- --port 5173 --strictPort
