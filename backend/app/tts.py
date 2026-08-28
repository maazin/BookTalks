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
    """Microsoft Edge's online TTS voices, via the edge-tts client."""

    def __init__(self, voice: str = None, rate: str = None):
        self.voice = voice or config.TTS_VOICE
        self.rate = rate or config.TTS_RATE

    async def _render_chunk(self, text: str, out_path: Path) -> None:
        last_error: Exception | None = None
        for attempt in range(1, config.TTS_MAX_RETRIES + 1):
            # If the destination has vanished, the document was deleted while
            # this page was in flight. Retrying can only fail again.
            if not out_path.parent.is_dir():
                raise TTSError("the destination folder no longer exists")
            try:
                communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
                await communicate.save(str(out_path))
                if out_path.exists() and out_path.stat().st_size > 0:
                    return
                raise TTSError("edge-tts returned an empty audio file")
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

        part_paths: List[Path] = []
        try:
            for index, chunk in enumerate(chunks):
                part = out_path.with_name(f"{out_path.stem}.part{index:03d}.mp3")
                await self._render_chunk(chunk, part)
                part_paths.append(part)
            # A page is itself a part of the finished book, so it carries no
            # header of its own.
            return await asyncio.to_thread(
                audio.concat_mp3s, part_paths, out_path, False
            )
        finally:
            for part in part_paths:
                part.unlink(missing_ok=True)


def get_engine(voice: str = None) -> Engine:
    """The single place that decides which TTS backend the app uses."""
    return EdgeTTSEngine(voice=voice)
