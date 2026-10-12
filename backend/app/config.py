"""Runtime configuration.

Local use needs none of this to be set — every value here has a default that
keeps the app working with zero configuration. The auth settings only turn on
once BOOKTALKS_PASSWORD is set, which is what a public deployment needs and a
laptop doesn't.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DATA_DIR = Path(os.getenv("BOOKTALKS_DATA_DIR", BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = Path(os.getenv("BOOKTALKS_DB_PATH", DATA_DIR / "booktalks.db")).resolve()

# One hardcoded voice, per PRD non-goals (no voice picker in v1).
TTS_VOICE = os.getenv("BOOKTALKS_TTS_VOICE", "en-US-AriaNeural")
TTS_RATE = os.getenv("BOOKTALKS_TTS_RATE", "+0%")

# edge-tts occasionally drops a connection; retry a chunk before giving up on it.
TTS_MAX_RETRIES = int(os.getenv("BOOKTALKS_TTS_RETRIES", "3"))
# Long pages are split into chunks at sentence boundaries for reliability.
TTS_CHUNK_CHARS = int(os.getenv("BOOKTALKS_TTS_CHUNK_CHARS", "2200"))
# How many TTS requests are in flight at once (counted in chunks, not pages —
# see tts.EdgeTTSEngine). This is almost pure network waiting, so it scales
# well past what feels intuitive. Measured on a real 345-page book, pages/sec:
#
#     5 -> 1.7    10 -> 2.8    16 -> 4.7-5.4    24 -> 6.1    32 -> 9.3
#
# with zero failures throughout; past ~32 the numbers get noisy without
# reliably getting better. 16 is the default rather than the peak: it's a ~3x
# speedup over the old value of 5, sits well inside the range that never
# errored, and leaves headroom on small instances, where every extra
# connection costs memory and TLS handshake CPU. Raise it if your host can
# take it; lower it if the service starts refusing requests.
TTS_CONCURRENCY = max(1, int(os.getenv("BOOKTALKS_TTS_CONCURRENCY", "16")))

MAX_UPLOAD_BYTES = int(os.getenv("BOOKTALKS_MAX_UPLOAD_MB", "200")) * 1024 * 1024


# --- local TTS fallback -----------------------------------------------------
# A second engine that runs on this machine, so an outage at Microsoft's end
# can't stop every conversion. edge-tts is still preferred — better voices,
# no local compute — and this only takes over when edge-tts looks broken
# service-wide rather than flaky on one page. See tts.FallbackEngine.
#
# piper-tts is an OPTIONAL dependency (backend/requirements-local-tts.txt),
# deliberately not in the Docker image: measured at 387 MB peak RSS and, at
# Render free's 0.1 CPU, only ~1.7x realtime, neither of which fits in 512 MB
# or any reasonable wait. Where it isn't installed the app behaves exactly as
# it did before — edge-tts only. See the README for the full numbers.
LOCAL_TTS_ENABLED = os.getenv("BOOKTALKS_LOCAL_TTS", "true").lower() not in (
    "false", "0", "off", "no",
)
# Piper voice, downloaded on first use into DATA_DIR/piper (63 MB, ~4s). Never
# committed — see the README. `python -m piper.download_voices` lists the rest.
LOCAL_TTS_VOICE = os.getenv("BOOKTALKS_LOCAL_TTS_VOICE", "en_US-lessac-medium")
LOCAL_TTS_MODEL_DIR = Path(
    os.getenv("BOOKTALKS_LOCAL_TTS_MODEL_DIR", DATA_DIR / "piper")
).resolve()
# Deliberately far below TTS_CONCURRENCY. That 16 is tuned for network wait;
# this engine is CPU-bound and onnxruntime already parallelizes internally, so
# extra threads buy almost nothing and cost a lot of memory. Measured on 8
# pages sharing one loaded model, pages/sec and peak RSS:
#
#     1 -> 0.33, 433 MB    2 -> 0.38, 668 MB
#     4 -> 0.43, 1147 MB   8 -> 0.40, 1304 MB
#
# 4 is only 1.3x faster than 1 for 2.6x the memory, and 8 is slower than 4.
LOCAL_TTS_CONCURRENCY = max(1, int(os.getenv("BOOKTALKS_LOCAL_TTS_CONCURRENCY", "2")))
# How many pages have to fail in a way that looks service-side (a 403, a
# refused handshake, DNS failure — see tts._looks_service_side) before the
# whole document switches to the local engine. One flaky chunk must not do it:
# a healthy edge-tts produces none of these, so a handful is already
# unambiguous, and the cost of being wrong is only these few pages.
LOCAL_TTS_FALLBACK_AFTER = max(
    1, int(os.getenv("BOOKTALKS_LOCAL_TTS_FALLBACK_AFTER", "3"))
)
# Once edge-tts has been written off, how long before a new document tries it
# again. Without this, one outage would keep every later book on the local
# engine until the server restarted.
LOCAL_TTS_RETRY_EDGE_AFTER_SEC = int(
    os.getenv("BOOKTALKS_LOCAL_TTS_RETRY_EDGE_AFTER_SEC", str(30 * 60))
)


# --- build identity ---------------------------------------------------------
# Which commit is actually running, reported by /api/health.
#
# This exists because "did my change deploy?" turned out to be genuinely hard
# to answer from outside: a deployment was once found serving a build many
# commits old while every push had succeeded. Guessing from the frontend
# bundle's content hash only works when a change happens to touch the
# frontend — a backend-only fix is invisible that way.

def _git_head() -> str:
    """The checked-out commit, read from .git directly.

    No subprocess: this runs at import, and a missing or broken git isn't
    worth an exception or the startup cost. Absent entirely in a container,
    where the environment variables below supply the answer instead.
    """
    git_dir = BASE_DIR / ".git"
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head  # detached HEAD is already the commit
        ref = head[5:].strip()
        loose = git_dir / ref
        if loose.is_file():
            return loose.read_text().strip()
        # Freshly cloned or gc'd repos keep refs in packed-refs instead.
        for line in (git_dir / "packed-refs").read_text().splitlines():
            if line.endswith(f" {ref}"):
                return line.split()[0]
    except OSError:
        pass
    return ""


# RENDER_GIT_COMMIT is set by Render on every deploy; BOOKTALKS_COMMIT lets
# any other host say so explicitly.
COMMIT = (
    os.getenv("BOOKTALKS_COMMIT") or os.getenv("RENDER_GIT_COMMIT") or _git_head()
)[:12] or "unknown"

# A page needs at least this much text to count as "has a text layer".
MIN_CHARS_FOR_TEXT_LAYER = 40
# If fewer than this fraction of pages have real text, treat the PDF as scanned.
MIN_TEXT_PAGE_RATIO = 0.2

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "BOOKTALKS_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    ).split(",")
    if o.strip()
]


# --- access control ---------------------------------------------------------
# Unset (the default) means no login is required, which is right for a tool
# that only ever listens on localhost. Set BOOKTALKS_PASSWORD to require one —
# do this before putting the app behind a public URL.
AUTH_PASSWORD = os.getenv("BOOKTALKS_PASSWORD") or None
# Signs session cookies. Falls back to the password itself so a minimal
# deployment only has to set one secret; set it separately if you'd rather
# sessions survive a password change.
AUTH_SESSION_SECRET = os.getenv("BOOKTALKS_SESSION_SECRET") or AUTH_PASSWORD or ""
AUTH_SESSION_DAYS = int(os.getenv("BOOKTALKS_SESSION_DAYS", "30"))
# Marks the session cookie Secure (HTTPS-only). Turn on for any public
# deployment; leave off for plain-HTTP local use, where the browser would
# silently refuse to store a Secure cookie at all.
SECURE_COOKIES = os.getenv("BOOKTALKS_SECURE_COOKIES", "false").lower() == "true"
# Failed logins allowed per IP within the window below before a 429.
AUTH_RATE_LIMIT = int(os.getenv("BOOKTALKS_AUTH_RATE_LIMIT", "10"))
AUTH_RATE_WINDOW_SEC = int(os.getenv("BOOKTALKS_AUTH_RATE_WINDOW_SEC", "300"))


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
