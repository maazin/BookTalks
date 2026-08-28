#!/usr/bin/env bash
# Development: FastAPI with reload on :8000, Vite with HMR on :5173.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)"; exit 1; }

if [ ! -d backend/.venv ]; then
  echo "Creating Python virtualenv…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --quiet --upgrade pip
  backend/.venv/bin/pip install --quiet -r backend/requirements.txt
fi

[ -d frontend/node_modules ] || (cd frontend && npm install)

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd backend && ../backend/.venv/bin/python -m uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &

echo
echo "BookTalks is starting…"
echo "  App: http://localhost:5173"
echo "  API: http://localhost:8000/api/health"
wait
