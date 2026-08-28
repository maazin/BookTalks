"""Every SQL statement in the app lives here."""
import sqlite3
from typing import Any, Dict, List, Optional

from .db import get_conn


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


# --- documents -------------------------------------------------------------

def create_document(filename: str, voice: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO documents (filename, voice, status) VALUES (?, ?, 'pending')",
            (filename, voice),
        )
        return int(cur.lastrowid)


def set_document_status(
    document_id: int, status: str, error_message: Optional[str] = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, document_id),
        )


def set_page_count(document_id: int, page_count: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET page_count = ? WHERE id = ?",
            (page_count, document_id),
        )


def finish_document(document_id: int, total_duration_sec: float) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE documents
                  SET status = 'ready', total_duration_sec = ?, error_message = NULL
                WHERE id = ?""",
            (total_duration_sec, document_id),
        )


def get_document(document_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        doc = _row(
            conn.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        )
        if doc is None:
            return None
        counts = conn.execute(
            """SELECT
                   COUNT(*) AS total,
                   SUM(status = 'done')   AS done,
                   SUM(status = 'failed') AS failed
                 FROM pages WHERE document_id = ?""",
            (document_id,),
        ).fetchone()
        doc["pages_done"] = counts["done"] or 0
        doc["pages_failed"] = counts["failed"] or 0
        doc["pages_total"] = counts["total"] or 0
        return doc


def list_documents() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.*,
                      COALESCE(SUM(p.status = 'done'), 0)   AS pages_done,
                      COALESCE(SUM(p.status = 'failed'), 0) AS pages_failed,
                      COUNT(p.id)                            AS pages_total
                 FROM documents d
                 LEFT JOIN pages p ON p.document_id = d.id
                GROUP BY d.id
                ORDER BY d.upload_date DESC, d.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def delete_document(document_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cur.rowcount > 0


# --- pages -----------------------------------------------------------------

def insert_pages(document_id: int, texts: List[str]) -> None:
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO pages (document_id, page_number, text, status)
               VALUES (?, ?, ?, 'pending')""",
            [(document_id, i + 1, text) for i, text in enumerate(texts)],
        )


def get_pages(document_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, page_number, text, audio_path, start_time_sec,
                      duration_sec, status
                 FROM pages WHERE document_id = ? ORDER BY page_number""",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_page_done(
    document_id: int, page_number: int, audio_path: str, duration_sec: float
) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE pages
                  SET audio_path = ?, duration_sec = ?, status = 'done'
                WHERE document_id = ? AND page_number = ?""",
            (audio_path, duration_sec, document_id, page_number),
        )


def mark_page_failed(document_id: int, page_number: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE pages
                  SET status = 'failed', audio_path = NULL, duration_sec = NULL
                WHERE document_id = ? AND page_number = ?""",
            (document_id, page_number),
        )


def set_page_offsets(document_id: int, offsets: Dict[int, float]) -> None:
    """Write each page's start time into the concatenated audio file."""
    with get_conn() as conn:
        conn.executemany(
            "UPDATE pages SET start_time_sec = ? WHERE document_id = ? AND page_number = ?",
            [(start, document_id, page) for page, start in offsets.items()],
        )


# --- playback state --------------------------------------------------------

def get_playback(document_id: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT position_sec, playback_rate, updated_at FROM playback_state WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return {"position_sec": 0.0, "playback_rate": 1.0, "updated_at": None}
        return dict(row)


def upsert_playback(
    document_id: int, position_sec: float, playback_rate: float
) -> Dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO playback_state (document_id, position_sec, playback_rate, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(document_id) DO UPDATE SET
                   position_sec = excluded.position_sec,
                   playback_rate = excluded.playback_rate,
                   updated_at = CURRENT_TIMESTAMP""",
            (document_id, position_sec, playback_rate),
        )
    return get_playback(document_id)
