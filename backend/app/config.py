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
# Pages narrated at once. Each is an independent request, so a handful in
# flight turns a long book from a serial crawl into a short wait; too many
# invites rate limiting.
TTS_CONCURRENCY = max(1, int(os.getenv("BOOKTALKS_TTS_CONCURRENCY", "5")))

MAX_UPLOAD_BYTES = int(os.getenv("BOOKTALKS_MAX_UPLOAD_MB", "200")) * 1024 * 1024

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
