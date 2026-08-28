"""Text-to-speech behind a one-method interface.

v1 uses edge-tts (free, no API key). If it ever gets unreliable, implement
another Engine here (e.g. Kokoro-82M running locally) and change `get_engine()`
— nothing outside this file knows which engine is in use.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import List, Protocol

import edge_tts

from . import audio, config

log = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_HAS_SPEECH = re.compile(r"[A-Za-z0-9]")


class TTSError(Exception):
    pass


class _DocumentDeleted(Exception):
    """Internal signal that the destination folder vanished mid-render — not
    a transport failure, so it skips the retry/backoff path below."""


class Engine(Protocol):
    async def synthesize(self, text: str, out_path: Path) -> float:
        """Render `text` to an mp3 at `out_path`; return its duration in seconds."""


def _split_long(sentence: str, limit: int) -> List[str]:
    """Break a single over-long sentence on whitespace."""
    parts: List[str] = []
    while len(sentence) > limit:
        cut = sentence.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(sentence[:cut].strip())
        sentence = sentence[cut:].strip()
    if sentence:
        parts.append(sentence)
    return parts


def chunk_text(text: str, limit: int | None = None) -> List[str]:
    """Split text into request-sized chunks at sentence boundaries."""
    limit = limit or config.TTS_CHUNK_CHARS
    sentences: List[str] = []
    for raw in _SENTENCE_SPLIT.split(text or ""):
        sentence = (raw or "").strip()
        if sentence:
            sentences.extend(_split_long(sentence, limit))

    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= limit:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


class EdgeTTSEngine:
    """Microsoft Edge's online TTS voices, via the edge-tts client.

    Every call opens a fresh websocket connection to Microsoft's service —
    the library has no way to reuse one — which costs roughly a second of
    handshake overhead before any audio comes back, on top of however long
    the text itself takes to speak. `semaphore` bounds how many of those
    connections are open at once across an entire document: shared across
    every page and, importantly, every chunk within a page, so a dense page
    that needs 3 requests doesn't quietly buy itself 3x the concurrency
    budget of a page that needs 1.
    """

    def __init__(
        self,
        voice: str = None,
        rate: str = None,
        semaphore: asyncio.Semaphore = None,
    ):
        self.voice = voice or config.TTS_VOICE
        self.rate = rate or config.TTS_RATE
        # A fresh semaphore when none is supplied — standalone use (tests,
        # scripts) still gets sane bounded concurrency for multi-chunk text.
        self.semaphore = semaphore or asyncio.Semaphore(config.TTS_CONCURRENCY)

    async def _render_chunk(self, text: str, out_path: Path) -> None:
        last_error: Exception | None = None
        for attempt in range(1, config.TTS_MAX_RETRIES + 1):
            try:
                # Held only around the network call, not the retry backoff
                # below — a chunk waiting out a failure shouldn't sit on a
                # concurrency slot that other chunks could be using.
                async with self.semaphore:
                    # Checked here, freshly, right as each slot is actually
                    # granted — not before waiting for one. With hundreds of
                    # chunks all queued on the same semaphore, checking only
                    # at task start would mean everything passes the check in
                    # the first instant and then never looks again; a document
                    # deleted mid-book wouldn't be noticed until the very end.
                    # Checking here instead gives the same responsiveness the
                    # old page-level-only concurrency gate had: a deletion
                    # gets caught within roughly one call's duration, at
                    # whatever rate slots are actually turning over.
                    if not out_path.parent.is_dir():
                        raise _DocumentDeleted()
                    communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
                    await communicate.save(str(out_path))
                if out_path.exists() and out_path.stat().st_size > 0:
                    return
                raise TTSError("edge-tts returned an empty audio file")
            except _DocumentDeleted:
                # Not a transport failure — retrying only wastes a backoff
                # cycle on something that can't succeed differently next time.
                # Deliberately outside the generic except below.
                out_path.unlink(missing_ok=True)
                raise TTSError("the destination folder no longer exists") from None
            except Exception as exc:  # noqa: BLE001 - retry any transport failure
                last_error = exc
                out_path.unlink(missing_ok=True)
                if attempt < config.TTS_MAX_RETRIES:
                    backoff = 2 ** attempt
                    log.warning(
                        "edge-tts attempt %s/%s failed (%s); retrying in %ss",
                        attempt, config.TTS_MAX_RETRIES, exc, backoff,
                    )
                    await asyncio.sleep(backoff)
        raise TTSError(f"edge-tts failed after {config.TTS_MAX_RETRIES} attempts: {last_error}")

    async def synthesize(self, text: str, out_path: Path) -> float:
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Blank or punctuation-only pages still need a slot on the timeline.
        if not _HAS_SPEECH.search(text or ""):
            return await asyncio.to_thread(audio.write_silence, out_path)

        chunks = chunk_text(text)
        if len(chunks) == 1:
            await self._render_chunk(chunks[0], out_path)
            return await asyncio.to_thread(audio.duration_sec, out_path)

        # Chunks are independent renders that only need to land in order at
        # the end — running them concurrently (still bounded by the shared
        # semaphore above) rather than one after another is what keeps a
        # dense page from taking several times as long as a plain one. This
        # used to be a sequential loop; on a real multi-chunk page that meant
        # paying the ~1s-per-call connection overhead serially, in full, once
        # per chunk — an 11-second page that should have taken 2.
        part_paths = [
            out_path.with_name(f"{out_path.stem}.part{index:03d}.mp3")
            for index in range(len(chunks))
        ]
        tasks = [
            asyncio.ensure_future(self._render_chunk(chunk, part))
            for chunk, part in zip(chunks, part_paths)
        ]
        try:
            try:
                await asyncio.gather(*tasks)
            except Exception:
                # One chunk failing for good means the page has failed; don't
                # leave its siblings still running in the background.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            # A page is itself a part of the finished book, so it carries no
            # header of its own.
            return await asyncio.to_thread(
                audio.concat_mp3s, part_paths, out_path, False
            )
        finally:
            for part in part_paths:
                part.unlink(missing_ok=True)


def get_engine(voice: str = None, semaphore: asyncio.Semaphore = None) -> Engine:
    """The single place that decides which TTS backend the app uses."""
    return EdgeTTSEngine(voice=voice, semaphore=semaphore)
