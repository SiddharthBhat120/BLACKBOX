#!/usr/bin/env bash
# BlackBox start script for macOS / Linux. Needs Python 3.10+.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r backend/requirements.txt
[ -f .env ] || cp .env.example .env
echo "BlackBox is starting at http://127.0.0.1:8001"
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001
