"""BookTalks API — upload a PDF, get an audiobook."""
import logging
import mimetypes
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List

import fitz
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, pipeline, store

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("booktalks")

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    log.info("Data directory: %s", config.DATA_DIR)
    yield


app = FastAPI(title="BookTalks", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
STREAM_CHUNK = 256 * 1024


# --- schemas ---------------------------------------------------------------

class PlaybackIn(BaseModel):
    position_sec: float = Field(ge=0)
    playback_rate: float = Field(default=1.0, ge=0.25, le=4.0)


# --- helpers ---------------------------------------------------------------

def _require_document(document_id: int) -> Dict[str, Any]:
    doc = store.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _document_payload(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": doc["id"],
        "filename": doc["filename"],
        "upload_date": doc["upload_date"],
        "page_count": doc["page_count"],
        "status": doc["status"],
        "error_message": doc["error_message"],
        "total_duration_sec": doc["total_duration_sec"],
        "pages_done": doc.get("pages_done", 0),
        "pages_failed": doc.get("pages_failed", 0),
    }


def _file_iterator(path: Path, start: int, end: int) -> Generator[bytes, None, None]:
    """Yield bytes [start, end] inclusive."""
    remaining = end - start + 1
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


# --- endpoints -------------------------------------------------------------

@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/documents", status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
) -> Dict[str, Any]:
    filename = os.path.basename(file.filename or "document.pdf")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    config.ensure_dirs()
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".pdf", dir=config.UPLOAD_DIR)
    tmp_path = Path(tmp_name)
    size = 0
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"PDF is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                out.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        # Fail fast on anything that isn't a readable PDF.
        try:
            with fitz.open(tmp_path) as doc:
                if doc.is_encrypted and not doc.authenticate(""):
                    raise HTTPException(
                        status_code=400, detail="This PDF is password protected."
                    )
                page_count = doc.page_count
            if page_count == 0:
                raise HTTPException(status_code=400, detail="This PDF has no pages.")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail="This file isn't a readable PDF."
            ) from exc

        document_id = store.create_document(filename)
        store.set_page_count(document_id, page_count)
        shutil.move(tmp_path, pipeline.document_pdf_path(document_id))
    finally:
        tmp_path.unlink(missing_ok=True)

    background_tasks.add_task(pipeline.process_document, document_id)
    return {"id": document_id, "status": "pending", "filename": filename}


@app.get("/api/documents")
def list_documents() -> List[Dict[str, Any]]:
    return [_document_payload(doc) for doc in store.list_documents()]


@app.get("/api/documents/{document_id}")
def get_document(document_id: int) -> Dict[str, Any]:
    return _document_payload(_require_document(document_id))


@app.get("/api/documents/{document_id}/pages")
def get_pages(document_id: int) -> List[Dict[str, Any]]:
    _require_document(document_id)
    pages = store.get_pages(document_id)
    return [
        {
            "page_number": p["page_number"],
            "start_time_sec": p["start_time_sec"],
            "duration_sec": p["duration_sec"],
            "status": p["status"],
            "preview": " ".join((p["text"] or "").split())[:120],
        }
        for p in pages
    ]


@app.get("/api/documents/{document_id}/audio")
def stream_audio(document_id: int, request: Request) -> Response:
    doc = _require_document(document_id)
    path = pipeline.full_audio_path(document_id)
    if doc["status"] != "ready" or not path.exists():
        raise HTTPException(status_code=409, detail="Audio isn't ready yet.")

    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }

    if not range_header:
        return FileResponse(path, media_type="audio/mpeg", headers=headers)

    match = RANGE_RE.fullmatch(range_header.strip())
    if not match:
        # Anything we don't understand (multipart ranges, junk) is allowed to be
        # answered with the whole file rather than an error.
        return FileResponse(path, media_type="audio/mpeg", headers=headers)

    raw_start, raw_end = match.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else file_size - 1
    else:
        # Suffix range: "bytes=-500" means the last 500 bytes.
        if not raw_end:
            return FileResponse(path, media_type="audio/mpeg", headers=headers)
        start = max(file_size - int(raw_end), 0)
        end = file_size - 1

    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        return Response(
            status_code=416, headers={"Content-Range": f"bytes */{file_size}"}
        )

    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
        }
    )
    return StreamingResponse(
        _file_iterator(path, start, end),
        status_code=206,
        media_type="audio/mpeg",
        headers=headers,
    )


@app.get("/api/documents/{document_id}/playback")
def get_playback(document_id: int) -> Dict[str, Any]:
    _require_document(document_id)
    return store.get_playback(document_id)


@app.put("/api/documents/{document_id}/playback")
def put_playback(document_id: int, body: PlaybackIn) -> Dict[str, Any]:
    _require_document(document_id)
    return store.upsert_playback(document_id, body.position_sec, body.playback_rate)


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: int) -> Response:
    _require_document(document_id)
    store.delete_document(document_id)
    pipeline.remove_document_files(document_id)
    return Response(status_code=204)


# --- static frontend (production build, when present) ----------------------

_STATIC_DIR = Path(
    os.getenv("BOOKTALKS_STATIC_DIR", config.BASE_DIR / "frontend" / "dist")
)
if _STATIC_DIR.is_dir():
    mimetypes.add_type("application/javascript", ".js")

    class SPAStaticFiles(StaticFiles):
        """Serve the built SPA, falling back to index.html for client routes."""

        async def get_response(self, path: str, scope):  # type: ignore[override]
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException:
                if path.startswith("api/"):
                    raise
                return await super().get_response("index.html", scope)

    app.mount("/", SPAStaticFiles(directory=_STATIC_DIR, html=True), name="static")
    log.info("Serving frontend from %s", _STATIC_DIR)
