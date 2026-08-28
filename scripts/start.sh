#!/usr/bin/env bash
# Single-process run: build the frontend, then let FastAPI serve both the API
# and the built app on http://localhost:8000.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)"; exit 1; }

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --quiet --upgrade pip
  backend/.venv/bin/pip install --quiet -r backend/requirements.txt
fi

[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd frontend && npm run build)

echo
echo "BookTalks: http://localhost:8000"
cd backend && exec ../backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
