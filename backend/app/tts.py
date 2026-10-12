"""Text-to-speech behind a one-method interface.

Two engines live here. edge-tts (free, no API key, good voices) is preferred
and does the work normally. Piper runs entirely on this machine and exists
because edge-tts depends on a service that can refuse everyone at once: in
October 2026 Microsoft retired the host it had always used and every page of
every book failed with a 403 until edge_endpoint.py repointed it. That
repoint works, but it is a patch on someone else's unreleased fix, and the
exposure it papers over is permanent — when the service says no, a
single-engine app converts nothing at all.

`get_engine()` is still the only place that decides, and still the only thing
outside this file that has to know an engine choice exists. What it returns
now is usually a FallbackEngine: edge-tts, watching itself, able to hand the
rest of a document to Piper mid-book if the failures it is seeing look like
the service rather than the page.

piper-tts is an optional dependency and is deliberately absent from the
Docker image — it does not fit on Render's free tier (see config and the
README for the measurements). Where it isn't installed, get_engine returns
plain edge-tts and nothing else changes.
"""
import asyncio
import importlib.util
import logging
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Set

import edge_tts

from . import audio, config, edge_endpoint

log = logging.getLogger(__name__)

# Must happen before the first Communicate() call. Microsoft retired the host
# edge-tts ships with, and every synthesis 403s without this — see
# edge_endpoint for the details.
edge_endpoint.apply()

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_HAS_SPEECH = re.compile(r"[A-Za-z0-9]")

# What narrated a document, recorded per document so a book spoken locally is
# distinguishable from one spoken by Microsoft's voices — they sound different,
# and "why does this one sound off?" should have a visible answer.
ENGINE_EDGE = "edge-tts"
ENGINE_LOCAL = "piper"

# Failures that mean the service is refusing everyone, not that this page is
# awkward. Status codes are matched with word boundaries on purpose: a 403-page
# book writes page_403.mp3, and a path in an unrelated error message must not
# read as an HTTP status.
_SERVICE_SIDE_STATUS = re.compile(r"\b(401|403|429|5\d\d)\b")
_SERVICE_SIDE_MARKERS = (
    "rejected websocket connection",
    "invalid status",
    "handshake",
    "getaddrinfo",
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "connection refused",
    "cannot connect to host",
)


def _looks_service_side(exc: BaseException) -> bool:
    """Is this the service saying no to everybody, or just a bad page?

    The distinction is the whole point of the fallback: switching engines on
    one flaky chunk would trade good voices for worse ones over nothing, while
    not switching during an outage leaves the user with an empty audiobook.
    Nothing here matches a page whose text is merely difficult — those fail
    after the engine has already connected and been answered.
    """
    text = str(exc)
    lowered = text.lower()
    return bool(_SERVICE_SIDE_STATUS.search(text)) or any(
        marker in lowered for marker in _SERVICE_SIDE_MARKERS
    )


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

    label = ENGINE_EDGE

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

    async def _render_chunk(self, text: str, out_path: Path) -> float | None:
        """Render one chunk. Returns its duration in seconds when edge-tts's
        own timing metadata was available, else None (caller falls back to
        measuring the file directly).

        edge-tts already reports exactly this — the offset and duration of
        the last sentence boundary, in the same response that carries the
        audio — because it's how the library builds word-synced subtitles.
        Capturing it here means most pages never need to shell out to
        ffprobe just to answer "how long is this audio", which is one fewer
        process spawned per page. That's negligible wall-clock time on
        typical hardware, but on a CPU-throttled deployment (a free-tier
        instance capped at a fraction of a core, say) spawning a process is
        real, non-free work, and it was previously paid once for every
        single page regardless of how little content that page had.
        """
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
                    last_offset = last_duration = None
                    with open(out_path, "wb") as handle:
                        async for message in communicate.stream():
                            if message["type"] == "audio":
                                handle.write(message["data"])
                            elif message["type"] in ("SentenceBoundary", "WordBoundary"):
                                last_offset = message["offset"]
                                last_duration = message["duration"]
                if not (out_path.exists() and out_path.stat().st_size > 0):
                    raise TTSError("edge-tts returned an empty audio file")
                if last_offset is None:
                    return None
                # Ticks are 100ns each — Microsoft's usual convention (also
                # how edge-tts's own SubMaker converts these same fields).
                return (last_offset + last_duration) / 10_000_000
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
            duration = await self._render_chunk(chunks[0], out_path)
            if duration is not None:
                return duration
            # No metadata came back for some reason — measure the file
            # directly rather than guess.
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


# --- the local engine ------------------------------------------------------

# espeak-ng formats its data directory into a fixed 160-byte buffer and, when
# the path is longer than that, silently truncates, fails its own existence
# check and falls through to the path baked in on whatever machine built the
# wheel. The result is a cryptic total failure that looks exactly like a
# broken package:
#
#   Error processing file '/Users/runner/work/piper1-gpl/.../phontab'
#
# Measured threshold: 155 characters works, 160 does not. Pinning older
# piper-tts releases changes nothing — it is not a version bug. A deep
# BOOKTALKS_DATA_DIR or a venv nested a few levels down is enough to cross it,
# so rather than inherit a landmine the engine points espeak at a short
# symlink when the real path is too long.
_ESPEAK_PATH_BUDGET = 150

_piper_lock = threading.Lock()
# (voice, synthesis config) — loaded once, reused for every page.
_piper_voice = None


def _short_espeak_data_dir() -> Optional[Path]:
    """A short-enough path to espeak's data, or None if the real one is fine."""
    from piper.phonemize_espeak import ESPEAK_DATA_DIR

    if len(str(ESPEAK_DATA_DIR)) <= _ESPEAK_PATH_BUDGET:
        return None

    link = Path(tempfile.gettempdir()) / "booktalks-espeak" / "espeak-ng-data"
    if len(str(link)) > _ESPEAK_PATH_BUDGET:
        # Nothing sensible left to try; let piper use its default and report
        # whatever it reports rather than pretending this helped.
        return None
    link.parent.mkdir(parents=True, exist_ok=True)
    # Replaced rather than reused: the venv can move, and a symlink left
    # pointing at a deleted install would fail in the same cryptic way.
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(ESPEAK_DATA_DIR, target_is_directory=True)
    log.info("espeak data path is too long for espeak-ng; using %s", link)
    return link


def local_engine_available() -> bool:
    """Is the local fallback installed and switched on?

    Cheap on purpose — no import of piper, no model download. Called to decide
    whether get_engine should bother wrapping anything, including on Render,
    where piper-tts is not in the image at all.
    """
    if not config.LOCAL_TTS_ENABLED:
        return False
    return importlib.util.find_spec("piper") is not None


def _download_voice(download_voice, name: str, model_dir: Path) -> None:
    """Fetch the voice model, atomically.

    Downloaded into a scratch directory and moved into place only once both
    files have arrived. A download interrupted half-way — a closed laptop, a
    dropped connection — would otherwise leave a truncated .onnx sitting at
    the path everything checks for, and since it exists and is non-empty it
    would never be re-fetched: the fallback would be permanently broken by the
    one thing it exists to survive.
    """
    staging = model_dir / f".{name}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        log.info("Downloading the local TTS voice %s (first use)", name)
        download_voice(name, staging)
        model_dir.mkdir(parents=True, exist_ok=True)
        for part in sorted(staging.iterdir()):
            part.replace(model_dir / part.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _load_piper_voice():
    """Load the Piper model and its synthesis settings, downloading the model
    on first use. Cached process-wide.

    Returns a (voice, synthesis config) pair so that nothing on the per-page
    render path has to import piper — the optional dependency is confined to
    the setup here, which runs once.

    The weights are not in the repo — 63 MB of model has no business in git —
    so the first narration that needs them fetches them into
    LOCAL_TTS_MODEL_DIR, which takes about four seconds. Every later page,
    document and restart reuses the file on disk.

    Called from a worker thread, hence the plain threading.Lock: two pages
    starting at once must not both load the model, which would briefly double
    the memory for no benefit.
    """
    global _piper_voice
    with _piper_lock:
        if _piper_voice is not None:
            return _piper_voice

        from piper import PiperVoice, SynthesisConfig
        from piper.download_voices import download_voice

        name = config.LOCAL_TTS_VOICE
        model_dir = config.LOCAL_TTS_MODEL_DIR
        model_path = model_dir / f"{name}.onnx"
        if not model_path.exists():
            _download_voice(download_voice, name, model_dir)

        kwargs = {}
        espeak_dir = _short_espeak_data_dir()
        if espeak_dir is not None:
            kwargs["espeak_data_dir"] = espeak_dir

        log.info("Loading the local TTS voice %s", name)
        # normalize_audio=False deliberately. With it on, Piper normalizes
        # each chunk's peak independently, which clips (measured max 0.0 dB)
        # and makes loudness jump from chunk to chunk through a book. Off is
        # steady at about -21 dB mean, which is where edge-tts sits too
        # (measured -21.2 dB against -21.4 dB on the same book), so a
        # document narrated by both engines doesn't change volume at the join.
        _piper_voice = (
            PiperVoice.load(model_path, **kwargs),
            SynthesisConfig(normalize_audio=False),
        )
        return _piper_voice


class PiperEngine:
    """Piper, running on this machine. No network, no service to refuse us.

    Quality is a step below Microsoft's neural voices and it costs real CPU
    and memory, which is why it is the fallback and not the default. What it
    buys is that it cannot have an outage.

    Synthesis is synchronous and CPU-bound, so it runs in a worker thread and
    is bounded by its own semaphore — a small one. The edge-tts default of 16
    is sized for connections sitting idle on the network; onnxruntime already
    parallelizes inside a single call, so here extra threads mostly buy memory
    (measured: 4 workers are 1.3x faster than 1 for 2.6x the RSS, and 8 are
    slower than 4). See config.LOCAL_TTS_CONCURRENCY.
    """

    label = ENGINE_LOCAL

    def __init__(self, semaphore: asyncio.Semaphore = None):
        self.semaphore = semaphore or asyncio.Semaphore(config.LOCAL_TTS_CONCURRENCY)

    def _render(self, text: str, out_path: Path) -> float:
        voice, syn_config = _load_piper_voice()

        # Piper hands back a chunk per sentence; joined here as raw PCM and
        # encoded once, so a page costs one ffmpeg process rather than one
        # per sentence. A page of prose is a few MB of int16 at this rate.
        parts: List[bytes] = []
        sample_rate = voice.config.sample_rate
        for chunk in voice.synthesize(text, syn_config=syn_config):
            parts.append(chunk.audio_int16_bytes)
            sample_rate = chunk.sample_rate

        if not parts:
            raise TTSError("the local TTS engine produced no audio")

        return audio.encode_pcm_to_mp3(b"".join(parts), out_path, sample_rate)

    async def synthesize(self, text: str, out_path: Path) -> float:
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Same contract as the edge-tts engine: a blank page still occupies a
        # slot on the timeline.
        if not _HAS_SPEECH.search(text or ""):
            return await asyncio.to_thread(audio.write_silence, out_path)

        async with self.semaphore:
            # Checked as the slot is granted, for the same reason the edge
            # engine does it: with a whole book queued here, a check at task
            # start would pass for everything in the first instant and never
            # look again.
            if not out_path.parent.is_dir():
                raise TTSError("the destination folder no longer exists")
            try:
                return await asyncio.to_thread(self._render, text, out_path)
            except Exception:
                out_path.unlink(missing_ok=True)
                raise


# --- choosing, and changing, engines ---------------------------------------

# Set when a document gives up on edge-tts, so the next document doesn't pay
# the same discovery cost again. Process-wide and deliberately expiring: one
# outage must not pin every future book to the local engine until a restart.
_edge_down_at: Optional[float] = None


def _mark_edge_down() -> None:
    global _edge_down_at
    _edge_down_at = time.monotonic()


def edge_recently_down() -> bool:
    if _edge_down_at is None:
        return False
    return (time.monotonic() - _edge_down_at) < config.LOCAL_TTS_RETRY_EDGE_AFTER_SEC


def reset_edge_health() -> None:
    """Forget that edge-tts was ever down. For tests, and for a manual retry."""
    global _edge_down_at
    _edge_down_at = None


class FallbackEngine:
    """edge-tts, with the local engine taking over if the service goes down.

    The judgement this makes is when to stop trusting edge-tts. Switching on
    the first failure would be wrong: pages fail for their own reasons, and
    trading good voices for worse ones over one dropped connection is a bad
    deal. Never switching is worse — that is the October 2026 failure, where
    every page 403s and the user is told only that "audio generation failed
    for every page".

    So the signal is a *pattern*: pages failing for reasons that cannot be
    about the page (see _looks_service_side), enough of them to rule out bad
    luck, with any success resetting the count. The threshold is capped at the
    document's own length, so a two-page PDF doesn't have to fail three times
    to qualify — on a whole-document wipeout there is nothing left to be
    cautious about.

    Switching is one-way within a document. Once the service has been written
    off, going back mid-book would risk a book narrated in two alternating
    voices, which is worse than one consistent fallback voice.
    """

    def __init__(
        self,
        primary: Engine,
        local_factory: Callable[[], Engine],
        page_count: int = 0,
        start_local: bool = False,
    ):
        self._primary = primary
        self._local_factory = local_factory
        self._local: Optional[Engine] = None
        self._local_lock = asyncio.Lock()
        self._switched = start_local
        self._service_failures = 0
        # A document shorter than the threshold can never reach it, and the
        # whole document failing is exactly the signal we're looking for.
        self._threshold = max(
            1,
            min(config.LOCAL_TTS_FALLBACK_AFTER, page_count or config.LOCAL_TTS_FALLBACK_AFTER),
        )
        # Which engines actually produced audio, for the record kept per
        # document. A book that tripped mid-way honestly holds both.
        self.engines_used: Set[str] = set()

    @property
    def using_local(self) -> bool:
        return self._switched

    async def _local_engine(self) -> Engine:
        # Built on demand so a document that never needs it never loads a
        # model or pays the memory.
        async with self._local_lock:
            if self._local is None:
                self._local = self._local_factory()
            return self._local

    async def _synthesize_locally(self, text: str, out_path: Path) -> float:
        engine = await self._local_engine()
        duration = await engine.synthesize(text, out_path)
        self.engines_used.add(ENGINE_LOCAL)
        return duration

    async def synthesize(self, text: str, out_path: Path) -> float:
        if self._switched:
            return await self._synthesize_locally(text, out_path)

        try:
            duration = await self._primary.synthesize(text, out_path)
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            if not _looks_service_side(exc):
                # A page problem stays a page problem: one page fails, the
                # document carries on, and the voice doesn't change.
                raise
            if self._switched:
                # This page raced the switch. Pages are narrated concurrently,
                # so during an outage every page in flight fails at once and
                # arrives here together — a whole book's worth. Another of
                # them has already established that the service is down, so
                # this one quietly goes local instead of re-deciding and
                # re-announcing it. Without this guard a 300-page book logs
                # 300 near-identical "falling back" errors, each claiming a
                # higher failure count than the three that actually decided.
                return await self._synthesize_locally(text, out_path)
            self._service_failures += 1
            if self._service_failures < self._threshold:
                log.warning(
                    "edge-tts failed in a way that looks service-side "
                    "(%s/%s before falling back): %s",
                    self._service_failures, self._threshold, exc,
                )
                raise
            log.error(
                "edge-tts has failed %s times for reasons that aren't about the "
                "page; narrating the rest of this document locally. Last error: %s",
                self._service_failures, exc,
            )
            self._switched = True
            _mark_edge_down()
            return await self._synthesize_locally(text, out_path)

        # Only a success clears the suspicion, so a connection that drops
        # occasionally but mostly works never trips the switch.
        self._service_failures = 0
        self.engines_used.add(ENGINE_EDGE)
        return duration


def engine_label(engines_used: Set[str]) -> Optional[str]:
    """How to record what narrated a document. None when nothing did."""
    if not engines_used:
        return None
    # Deliberately both, in order, when a document tripped mid-book — it holds
    # pages in two voices and saying only one of them would be a lie.
    return "+".join(
        label for label in (ENGINE_EDGE, ENGINE_LOCAL) if label in engines_used
    )


def get_engine(
    voice: str = None,
    semaphore: asyncio.Semaphore = None,
    page_count: int = 0,
) -> Engine:
    """The single place that decides which TTS backend the app uses."""
    primary = EdgeTTSEngine(voice=voice, semaphore=semaphore)
    if not local_engine_available():
        # No local engine installed or it's switched off — exactly the
        # behaviour this app had before the fallback existed.
        return primary

    start_local = edge_recently_down()
    if start_local:
        log.info(
            "edge-tts was failing service-side recently; starting this document "
            "on the local engine"
        )
    return FallbackEngine(
        primary,
        local_factory=PiperEngine,
        page_count=page_count,
        start_local=start_local,
    )
