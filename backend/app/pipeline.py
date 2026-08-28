"""Background processing: PDF in, one narrated mp3 plus a page timeline out.

Single user, one document at a time — a module-level lock is the whole queue.
Within a document, pages are narrated a few at a time, since each page is an
independent network round trip and waiting on them one by one is what made
long books slow.
"""
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import audio, config, pdf_text, store, tts

log = logging.getLogger(__name__)

# Serializes documents so two uploads don't compete for the same TTS quota.
_job_lock = asyncio.Lock()


class DocumentGone(Exception):
    """The document was deleted while it was being processed."""


def document_audio_dir(document_id: int) -> Path:
    return config.AUDIO_DIR / str(document_id)


def document_pdf_path(document_id: int) -> Path:
    return config.UPLOAD_DIR / f"{document_id}.pdf"


def full_audio_path(document_id: int) -> Path:
    return document_audio_dir(document_id) / "full.mp3"


def remove_document_files(document_id: int) -> None:
    shutil.rmtree(document_audio_dir(document_id), ignore_errors=True)
    document_pdf_path(document_id).unlink(missing_ok=True)


def _ensure_present(document_id: int) -> None:
    if store.get_document(document_id) is None:
        raise DocumentGone(document_id)


async def process_document(document_id: int) -> None:
    """Entry point handed to FastAPI's BackgroundTasks."""
    async with _job_lock:
        try:
            await _process(document_id)
        except DocumentGone:
            log.info("Document %s was deleted mid-conversion; stopping", document_id)
            remove_document_files(document_id)
        except Exception as exc:  # noqa: BLE001 - never let a job kill the server
            log.exception("Processing failed for document %s", document_id)
            store.set_document_status(document_id, "failed", str(exc))


async def _process(document_id: int) -> None:
    pdf_path = document_pdf_path(document_id)
    if not pdf_path.exists():
        store.set_document_status(document_id, "failed", "Uploaded file is missing.")
        return

    doc = store.get_document(document_id)
    # Chosen at upload time and fixed from here on — narration is pre-rendered,
    # so there's no "changing the voice" after the fact without redoing it.
    voice = (doc or {}).get("voice") or config.TTS_VOICE

    # 1. Extract and clean.
    store.set_document_status(document_id, "extracting")
    try:
        pages = await asyncio.to_thread(pdf_text.extract_and_clean, pdf_path)
    except pdf_text.NoTextLayerError as exc:
        store.set_document_status(document_id, "failed", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("Extraction failed for document %s", document_id)
        store.set_document_status(
            document_id, "failed", f"Could not read this PDF: {exc}"
        )
        return

    _ensure_present(document_id)
    store.set_page_count(document_id, len(pages))
    store.insert_pages(document_id, pages)

    # 2. Narrate. One bad page must not sink the document.
    store.set_document_status(document_id, "generating_audio")
    out_dir = document_audio_dir(document_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    durations = await _narrate_pages(document_id, pages, out_dir, voice)

    if not durations:
        _ensure_present(document_id)
        store.set_document_status(
            document_id,
            "failed",
            "Audio generation failed for every page. Check your internet connection "
            "and try again.",
        )
        return

    # 3. Concatenate in page order, then work out where each page starts.
    parts = [
        out_dir / f"page_{index}.mp3"
        for index in range(1, len(pages) + 1)
        if index in durations
    ]

    _ensure_present(document_id)
    full_path = full_audio_path(document_id)
    total = await asyncio.to_thread(audio.concat_mp3s, parts, full_path)

    offsets = _page_offsets(len(pages), durations, total)

    _ensure_present(document_id)
    store.set_page_offsets(document_id, offsets)
    store.finish_document(document_id, total)
    log.info(
        "Document %s ready: %s pages, %.1f seconds", document_id, len(pages), total
    )


def _page_offsets(
    page_count: int, durations: Dict[int, float], total: float
) -> Dict[int, float]:
    """Where each page starts inside the concatenated file.

    Joining mp3s without re-encoding leaves each part's header frame in the
    stream as a few hundredths of a second of silence, so a plain running sum
    of page durations drifts later and later through a long book. The gap is
    the same at every join, so measuring the finished file once tells us
    exactly how much to add per page.
    """
    rendered = len(durations)
    gap = 0.0
    if rendered:
        measured_gap = (total - sum(durations.values())) / rendered
        # A frame is ~0.024s; anything wildly outside that means the
        # measurement is off, and no correction beats a bad one.
        gap = min(max(measured_gap, 0.0), 0.25)

    offsets: Dict[int, float] = {}
    running = 0.0
    seen = 0
    for index in range(1, page_count + 1):
        # A failed page still gets an offset, so jump-to-page lands on the next
        # page that does have audio.
        if index in durations:
            seen += 1
        # `seen - 1`: the page's own header frame is already part of its audio,
        # so only the joins ahead of it push the start time later.
        offsets[index] = round(min(running + max(seen - 1, 0) * gap, total), 3)
        if index in durations:
            running += durations[index]
    return offsets


async def _narrate_pages(
    document_id: int, pages: List[str], out_dir: Path, voice: str
) -> Dict[int, float]:
    """Render every page, a few at a time. Returns {page_number: duration}."""
    engine = tts.get_engine(voice)
    semaphore = asyncio.Semaphore(config.TTS_CONCURRENCY)
    tasks: List[asyncio.Task] = []
    gone = False

    def stop_everything() -> None:
        """The document was deleted — drop the pages still in flight rather
        than narrating (and retrying) a book nobody is waiting for."""
        nonlocal gone
        gone = True
        current = asyncio.current_task()
        for task in tasks:
            if task is not current and not task.done():
                task.cancel()

    async def render(index: int, text: str) -> Tuple[int, Optional[float]]:
        async with semaphore:
            if gone:
                return index, None
            # Checked per page so a deletion stops the job promptly.
            if store.get_document(document_id) is None:
                stop_everything()
                return index, None

            page_path = out_dir / f"page_{index}.mp3"
            try:
                duration = await engine.synthesize(text, page_path)
            except Exception as exc:  # noqa: BLE001
                if gone:
                    return index, None
                if store.get_document(document_id) is None:
                    # The failure is the deletion, not the page.
                    stop_everything()
                    return index, None
                log.error("Page %s of document %s failed: %s", index, document_id, exc)
                page_path.unlink(missing_ok=True)
                store.mark_page_failed(document_id, index)
                return index, None

            store.mark_page_done(document_id, index, str(page_path), duration)
            return index, duration

    tasks = [
        asyncio.create_task(render(index, text))
        for index, text in enumerate(pages, start=1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    if gone:
        raise DocumentGone(document_id)

    durations: Dict[int, float] = {}
    for result in results:
        if isinstance(result, BaseException):
            if not isinstance(result, asyncio.CancelledError):
                log.error("Unexpected narration failure: %s", result)
            continue
        index, duration = result
        if duration is not None:
            durations[index] = duration
    return durations
