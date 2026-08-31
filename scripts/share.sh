#!/usr/bin/env bash
# Serve BookTalks from this machine and expose it on a public HTTPS URL via a
# Cloudflare Tunnel, so someone else can use it from their phone or laptop.
#
# The app itself stays bound to 127.0.0.1 — it is never opened to your local
# network. cloudflared makes the outbound connection, so this needs no port
# forwarding and no router changes.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ffmpeg      >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)"; exit 1; }
command -v cloudflared >/dev/null || { echo "cloudflared is required (brew install cloudflared)"; exit 1; }

# This URL is reachable by anyone who has it, so a password is not optional.
if [ -z "${BOOKTALKS_PASSWORD:-}" ]; then
  cat <<'MSG'
Refusing to start: BOOKTALKS_PASSWORD is not set.

This command puts BookTalks on a public URL. Without a password, anyone who
has (or guesses) that link could read, upload, and delete your library.

Pick a password and run again:

    BOOKTALKS_PASSWORD='something-only-you-two-know' ./scripts/share.sh
MSG
  exit 1
fi

# The tunnel terminates HTTPS, so the session cookie can be HTTPS-only.
export BOOKTALKS_SECURE_COOKIES=true
PORT="${PORT:-8000}"

if [ ! -d backend/.venv ]; then
  echo "Creating Python virtualenv…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --quiet --upgrade pip
  backend/.venv/bin/pip install --quiet -r backend/requirements.txt
fi
[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd frontend && npm run build)

LOG="$(mktemp -t booktalks-tunnel)"
cleanup() { kill 0 2>/dev/null || true; rm -f "$LOG"; }
trap cleanup EXIT INT TERM

echo "Starting BookTalks on 127.0.0.1:${PORT}…"
(cd backend && exec ../backend/.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$PORT") &

# Wait for the app to answer before opening it to the world.
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && break
  sleep 1
done

echo "Opening the tunnel…"
cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate > "$LOG" 2>&1 &

URL=""
for _ in $(seq 1 60); do
  URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "$URL" ]; then
  echo "Couldn't get a tunnel URL. Last output:"; tail -20 "$LOG"; exit 1
fi

cat <<MSG

  ────────────────────────────────────────────────────────
   BookTalks is live at:

     $URL

   Share that link and the password you set.
   On her phone: open it, then Share → Add to Home Screen
   so it opens like an app and keeps playing on the lock screen.

   This link works only while this window stays open, and a
   NEW link is generated each time you run this. For a
   permanent address, see "A permanent link" in the README.

   Press Ctrl-C to stop sharing.
  ────────────────────────────────────────────────────────

MSG

wait
