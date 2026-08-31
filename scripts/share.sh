#!/usr/bin/env bash
# Serve BookTalks from this machine on a public HTTPS URL, so someone else can
# use it from their phone or laptop.
#
# The app itself stays bound to 127.0.0.1 — it is never opened to your local
# network. The tunnel dials out, so this needs no port forwarding and no
# router changes.
#
# Prefers a Tailscale Funnel, which gives a permanent address that never
# changes. Falls back to a Cloudflare quick tunnel, which needs no setup at
# all but hands out a different URL every run.
set -euo pipefail
cd "$(dirname "$0")/.."

TS_SOCK="${BOOKTALKS_TS_SOCKET:-$HOME/.booktalks-tailscale/tailscaled.sock}"
PORT="${PORT:-8000}"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required (brew install ffmpeg)"; exit 1; }

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

# Decide which tunnel to use before doing any work.
MODE="quick"
if command -v tailscale >/dev/null && [ -S "$TS_SOCK" ] \
   && tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
  MODE="funnel"
elif ! command -v cloudflared >/dev/null; then
  echo "Need either a logged-in Tailscale (for a permanent link) or cloudflared"
  echo "(for a temporary one). See 'Sharing it with someone else' in the README."
  exit 1
fi

# The tunnel terminates HTTPS in both modes, so the cookie can be HTTPS-only.
export BOOKTALKS_SECURE_COOKIES=true

if [ ! -d backend/.venv ]; then
  echo "Creating Python virtualenv…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --quiet --upgrade pip
  backend/.venv/bin/pip install --quiet -r backend/requirements.txt
fi
[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd frontend && npm run build)

LOG="$(mktemp -t booktalks-tunnel)"
cleanup() {
  # Take the funnel down so the URL stops answering when you stop sharing.
  [ "$MODE" = "funnel" ] && tailscale --socket="$TS_SOCK" funnel --https=443 off >/dev/null 2>&1 || true
  kill 0 2>/dev/null || true
  rm -f "$LOG"
}
trap cleanup EXIT INT TERM

echo "Starting BookTalks on 127.0.0.1:${PORT}…"
(cd backend && exec ../backend/.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$PORT") &

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && break
  sleep 1
done

if [ "$MODE" = "funnel" ]; then
  echo "Opening the Tailscale Funnel…"
  if ! tailscale --socket="$TS_SOCK" funnel --bg "$PORT" > "$LOG" 2>&1; then
    echo
    echo "Tailscale couldn't open the funnel. It usually says why below —"
    echo "most often Funnel or HTTPS needs enabling once in the admin console."
    echo
    cat "$LOG"
    exit 1
  fi
  HOST="$(tailscale --socket="$TS_SOCK" status --json \
          | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  URL="https://${HOST}"
  PERMANENT="yes"
else
  echo "Opening a Cloudflare quick tunnel…"
  cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate > "$LOG" 2>&1 &
  URL=""
  for _ in $(seq 1 60); do
    URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
    [ -n "$URL" ] && break
    sleep 1
  done
  [ -n "$URL" ] || { echo "Couldn't get a tunnel URL. Last output:"; tail -20 "$LOG"; exit 1; }
  PERMANENT="no"
fi

echo
echo "  ────────────────────────────────────────────────────────"
echo "   BookTalks is live at:"
echo
echo "     $URL"
echo
echo "   Share that link and the password you set."
echo "   On her phone: open it, then Share → Add to Home Screen,"
echo "   so it opens like an app and the lock-screen controls work."
echo
if [ "$PERMANENT" = "yes" ]; then
  echo "   This address is permanent — it stays the same every time,"
  echo "   so you only ever have to send it once."
else
  echo "   This link is temporary: a NEW one is generated each run."
  echo "   See \"A permanent link\" in the README to fix that."
fi
echo
echo "   It only works while this window stays open."
echo "   Press Ctrl-C to stop sharing."
echo "  ────────────────────────────────────────────────────────"
echo

wait
