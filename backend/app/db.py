"""SQLite access. One connection per operation; WAL so reads never block on the
background job's writes."""
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    filename           TEXT    NOT NULL,
    upload_date        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    page_count         INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'pending',
    error_message      TEXT,
    total_duration_sec REAL,
    voice              TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number    INTEGER NOT NULL,
    text           TEXT    NOT NULL DEFAULT '',
    audio_path     TEXT,
    start_time_sec REAL,
    duration_sec   REAL,
    status         TEXT    NOT NULL DEFAULT 'pending',
    UNIQUE (document_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_pages_document ON pages(document_id, page_number);

CREATE TABLE IF NOT EXISTS playback_state (
    document_id   INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    position_sec  REAL NOT NULL DEFAULT 0,
    playback_rate REAL NOT NULL DEFAULT 1.0,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # journal_mode is deliberately NOT set here: it's a persistent property of
    # the database file, not of a connection, so setting it once in init_db()
    # is enough. It was measured at ~0.33ms of the ~0.4ms each connection cost
    # — over 80% of the total — and narrating a long book opens thousands of
    # short-lived connections. foreign_keys and busy_timeout genuinely are
    # per-connection and do have to be set every time.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the initial release. CREATE TABLE IF NOT EXISTS only
# helps a brand-new database — an existing documents.db on someone's disk
# needs each of these added explicitly, once, without losing their library.
_MIGRATIONS = [
    ("documents", "voice", "ALTER TABLE documents ADD COLUMN voice TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, statement in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(statement)


def init_db() -> None:
    config.ensure_dirs()
    # Persistent, stored in the database file itself — set once here rather
    # than on every connection. See connect() for why that matters.
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # A document left mid-flight by a crash or restart can never finish on
        # its own — surface it as failed rather than polling forever.
        conn.execute(
            """UPDATE documents
                  SET status = 'failed',
                      error_message = COALESCE(error_message,
                          'Processing was interrupted — upload the PDF again.')
                WHERE status IN ('pending', 'extracting', 'generating_audio')"""
        )
