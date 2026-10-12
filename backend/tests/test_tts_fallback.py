"""Engine selection, and when edge-tts stops being trusted.

The fallback's whole value is in a judgement call: switch too eagerly and a
book gets the worse voice over one dropped connection; switch too reluctantly
and a Microsoft-side outage produces an empty audiobook and the message
"Audio generation failed for every page." These tests pin that line down.

Nothing here touches the network or loads a model. The local engine is a stub
throughout except where noted, and whether piper-tts happens to be installed
on the machine running the suite is always set explicitly — otherwise these
tests would mean different things on the Mac and in a container.
"""
import asyncio
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tts  # noqa: E402


# --- helpers ---------------------------------------------------------------

SERVICE_SIDE = tts.TTSError("edge-tts failed after 3 attempts: server rejected "
                            "WebSocket connection: HTTP 403")
PAGE_PROBLEM = tts.TTSError("edge-tts returned an empty audio file")


class FakeEdge:
    """Stands in for EdgeTTSEngine, failing to order."""

    def __init__(self, outcomes: List[object]):
        # Each entry is either an exception to raise or a float to return.
        self.outcomes = list(outcomes)
        self.calls = 0

    async def synthesize(self, text: str, out_path: Path) -> float:
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else 1.0
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeLocal:
    """Stands in for PiperEngine. Records that it was asked."""

    label = tts.ENGINE_LOCAL

    def __init__(self):
        self.calls = 0

    async def synthesize(self, text: str, out_path: Path) -> float:
        self.calls += 1
        return 42.0


@pytest.fixture(autouse=True)
def _clean_edge_health():
    """The down-latch is process-wide, so it must not leak between tests."""
    tts.reset_edge_health()
    yield
    tts.reset_edge_health()


def build(outcomes, page_count=100, start_local=False):
    edge = FakeEdge(outcomes)
    local = FakeLocal()
    engine = tts.FallbackEngine(
        edge, local_factory=lambda: local, page_count=page_count,
        start_local=start_local,
    )
    return engine, edge, local


def run(engine, pages, tmp_path):
    """Narrate `pages` pages one after another, collecting what happened."""
    async def go():
        results = []
        for index in range(pages):
            try:
                results.append(await engine.synthesize(
                    "Some text.", tmp_path / f"page_{index}.mp3"
                ))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)
        return results
    return asyncio.run(go())


# --- classifying failures --------------------------------------------------

@pytest.mark.parametrize("message", [
    "edge-tts failed after 3 attempts: server rejected WebSocket connection: HTTP 403",
    "InvalidStatus: HTTP 401",
    "429 Too Many Requests",
    "HTTP 503",
    "opening handshake failed",
    "[Errno 8] nodename nor servname provided, or not known",
    "socket.gaierror: getaddrinfo failed",
    "Temporary failure in name resolution",
    "Cannot connect to host api.msedgeservices.com:443",
    "ConnectionRefusedError: [Errno 61] Connection refused",
])
def test_service_side_failures_are_recognised(message):
    assert tts._looks_service_side(tts.TTSError(message))


@pytest.mark.parametrize("message", [
    "edge-tts returned an empty audio file",
    "the destination folder no longer exists",
    "Could not read the duration of page_7.mp3",
    # The reason status codes are matched with word boundaries: a 403-page book
    # writes page_403.mp3, and a path must never read as an HTTP status.
    "ffmpeg failed: no such file /data/audio/9/page_403.mp3",
    "AudioError: concat failed for page_500.mp3",
])
def test_page_problems_are_not_mistaken_for_an_outage(message):
    assert not tts._looks_service_side(tts.TTSError(message))


# --- when the switch trips -------------------------------------------------

def test_one_bad_page_does_not_change_engines(tmp_path):
    """A single service-side blip costs that page and nothing else — trading
    the good voices for the fallback on this evidence would be a bad deal."""
    engine, edge, local = build([SERVICE_SIDE] + [1.5] * 4)
    results = run(engine, 5, tmp_path)

    assert isinstance(results[0], tts.TTSError)   # that page failed
    assert results[1:] == [1.5] * 4               # the rest narrated normally
    assert local.calls == 0
    assert engine.using_local is False
    assert engine.engines_used == {tts.ENGINE_EDGE}


def test_a_run_of_service_side_failures_switches_to_local(tmp_path):
    """The October 2026 shape: every call 403s. The first few pages are spent
    establishing that, then the rest of the book is narrated locally."""
    engine, edge, local = build([SERVICE_SIDE] * 10, page_count=10)
    results = run(engine, 10, tmp_path)

    threshold = tts.config.LOCAL_TTS_FALLBACK_AFTER
    # Only the pages before the threshold are lost.
    assert all(isinstance(r, tts.TTSError) for r in results[: threshold - 1])
    assert results[threshold - 1:] == [42.0] * (10 - threshold + 1)
    assert engine.using_local is True
    assert local.calls == 10 - threshold + 1
    # edge-tts never produced audio, so it is not recorded as having narrated.
    assert engine.engines_used == {tts.ENGINE_LOCAL}


def test_pages_failing_on_their_own_content_never_switch(tmp_path):
    """However many pages fail for reasons that are about the page, the voice
    stays put. Only the service being down justifies changing it."""
    engine, edge, local = build([PAGE_PROBLEM] * 20, page_count=20)
    results = run(engine, 20, tmp_path)

    assert all(isinstance(r, tts.TTSError) for r in results)
    assert local.calls == 0
    assert engine.using_local is False
    assert engine.engines_used == set()


def test_a_success_clears_the_suspicion(tmp_path):
    """A connection that drops now and then but mostly works must not trip
    the switch — otherwise ordinary flakiness downgrades the voice."""
    threshold = tts.config.LOCAL_TTS_FALLBACK_AFTER
    # One short of the threshold, a success, then one short again, repeatedly.
    outcomes = ([SERVICE_SIDE] * (threshold - 1) + [1.0]) * 3
    engine, edge, local = build(outcomes, page_count=len(outcomes))
    run(engine, len(outcomes), tmp_path)

    assert engine.using_local is False
    assert local.calls == 0


def test_a_short_document_does_not_have_to_fail_three_times(tmp_path):
    """The threshold is capped at the document's own length. A two-page PDF
    can never reach a count of three, so without the cap it could never fall
    back at all — it would just fail entirely, which is the exact outcome
    this whole mechanism exists to prevent.

    The cap makes the signal "the whole document failed", so page 1 is still
    spent establishing that. The pipeline re-narrates it afterwards — see
    test_pages_lost_before_the_switch_are_narrated_locally below."""
    engine, edge, local = build([SERVICE_SIDE, SERVICE_SIDE], page_count=2)
    results = run(engine, 2, tmp_path)

    assert isinstance(results[0], tts.TTSError)
    assert results[1] == 42.0
    assert engine.using_local is True


def test_a_single_page_document_switches_immediately(tmp_path):
    engine, edge, local = build([SERVICE_SIDE], page_count=1)
    assert run(engine, 1, tmp_path) == [42.0]
    assert engine.using_local is True


def test_switching_is_one_way_within_a_document(tmp_path):
    """Once the service has been written off, edge-tts is not retried mid-book
    even if it recovers: alternating voices through one audiobook is worse
    than one consistent fallback voice."""
    engine, edge, local = build([SERVICE_SIDE] * 3 + [1.0] * 5, page_count=8)
    run(engine, 8, tmp_path)

    calls_at_switch = edge.calls
    assert engine.using_local is True
    # edge-tts was not consulted again after the switch.
    assert edge.calls == calls_at_switch
    assert local.calls == 8 - tts.config.LOCAL_TTS_FALLBACK_AFTER + 1


# --- what gets recorded against the document -------------------------------

def test_a_document_narrated_by_both_engines_says_so(tmp_path):
    """A book that tripped part-way genuinely holds pages in two voices, and
    naming only one of them would be a lie."""
    threshold = tts.config.LOCAL_TTS_FALLBACK_AFTER
    engine, edge, local = build([1.0, 1.0] + [SERVICE_SIDE] * threshold + [1.0] * 3,
                                page_count=10)
    run(engine, 10, tmp_path)

    assert engine.engines_used == {tts.ENGINE_EDGE, tts.ENGINE_LOCAL}
    assert tts.engine_label(engine.engines_used) == "edge-tts+piper"


def test_engine_label_reads_in_a_fixed_order():
    assert tts.engine_label(set()) is None
    assert tts.engine_label({tts.ENGINE_EDGE}) == "edge-tts"
    assert tts.engine_label({tts.ENGINE_LOCAL}) == "piper"
    # Chronological, not set-iteration order, which isn't stable.
    assert tts.engine_label({tts.ENGINE_LOCAL, tts.ENGINE_EDGE}) == "edge-tts+piper"


# --- choosing an engine ----------------------------------------------------

def test_without_a_local_engine_nothing_changes(monkeypatch):
    """On Render, piper-tts isn't in the image. get_engine must return exactly
    what it returned before the fallback existed."""
    monkeypatch.setattr(tts, "local_engine_available", lambda: False)
    engine = tts.get_engine("en-US-AriaNeural")

    assert isinstance(engine, tts.EdgeTTSEngine)
    assert not hasattr(engine, "engines_used")


def test_with_a_local_engine_edge_is_still_preferred(monkeypatch):
    monkeypatch.setattr(tts, "local_engine_available", lambda: True)
    engine = tts.get_engine("en-US-AriaNeural", page_count=50)

    assert isinstance(engine, tts.FallbackEngine)
    # Preferred, not merely available: nothing is local until edge-tts fails.
    assert engine.using_local is False


def test_switching_off_the_fallback_is_honoured(monkeypatch):
    monkeypatch.setattr(tts.config, "LOCAL_TTS_ENABLED", False)
    assert tts.local_engine_available() is False
    assert isinstance(tts.get_engine(), tts.EdgeTTSEngine)


def test_the_next_document_starts_local_while_edge_is_known_down(monkeypatch):
    """One document having proved the service is down shouldn't make the next
    one prove it again — that would cost the threshold's worth of pages every
    single time."""
    monkeypatch.setattr(tts, "local_engine_available", lambda: True)
    tts._mark_edge_down()

    engine = tts.get_engine(page_count=50)
    assert isinstance(engine, tts.FallbackEngine)
    assert engine.using_local is True


def test_edge_is_tried_again_once_the_outage_window_has_passed(monkeypatch):
    """The latch has to expire, or a single bad afternoon would pin every
    future book to the fallback voice until someone restarted the server."""
    monkeypatch.setattr(tts, "local_engine_available", lambda: True)
    tts._mark_edge_down()
    assert tts.edge_recently_down() is True

    monkeypatch.setattr(tts.config, "LOCAL_TTS_RETRY_EDGE_AFTER_SEC", 0)
    assert tts.edge_recently_down() is False
    assert tts.get_engine(page_count=50).using_local is False


def test_the_local_engine_is_not_built_unless_it_is_needed(monkeypatch, tmp_path):
    """Loading a 63 MB model for a document that never needs it would be a
    waste of memory on every single conversion."""
    built = []

    def factory():
        built.append(1)
        return FakeLocal()

    engine = tts.FallbackEngine(
        FakeEdge([1.0] * 5), local_factory=factory, page_count=5
    )
    run(engine, 5, tmp_path)
    assert built == []


# --- the local engine itself -----------------------------------------------

def test_a_blank_page_still_gets_a_slot_on_the_timeline(monkeypatch, tmp_path):
    """Same contract as the edge engine: offsets stay aligned with the audio
    only if every page occupies some time, even an empty one."""
    monkeypatch.setattr(tts.audio, "write_silence", lambda path: 0.4)
    # Proves it never reached synthesis — no model is loaded by this test.
    monkeypatch.setattr(
        tts, "_load_piper_voice",
        lambda: pytest.fail("a blank page should not load the model"),
    )
    engine = tts.PiperEngine(semaphore=asyncio.Semaphore(1))
    assert asyncio.run(engine.synthesize("   \n  ", tmp_path / "p.mp3")) == 0.4


def test_local_output_is_resampled_to_the_shared_format(monkeypatch, tmp_path):
    """Piper's medium voices run at 22050 Hz where edge-tts returns 24000.
    concat_mp3s joins pages with `-c copy`, so a page that kept its own rate
    would make the rest of a mixed-engine book play at the wrong pitch."""
    class FakeChunk:
        audio_int16_bytes = b"\x01\x02" * 100
        sample_rate = 22050

    class FakeVoice:
        config = type("C", (), {"sample_rate": 22050})()

        def synthesize(self, text, syn_config=None):
            yield FakeChunk()
            yield FakeChunk()

    monkeypatch.setattr(tts, "_load_piper_voice", lambda: (FakeVoice(), None))
    seen = {}

    def fake_encode(pcm, out_path, source_rate, xing=False):
        seen.update(pcm=pcm, source_rate=source_rate)
        return 3.5

    monkeypatch.setattr(tts.audio, "encode_pcm_to_mp3", fake_encode)

    engine = tts.PiperEngine(semaphore=asyncio.Semaphore(1))
    duration = asyncio.run(engine.synthesize("Hello.", tmp_path / "p.mp3"))

    assert duration == 3.5
    assert seen["source_rate"] == 22050
    # Both chunks, joined once, so a page costs one ffmpeg process not two.
    assert seen["pcm"] == b"\x01\x02" * 200


def test_a_local_engine_that_produces_nothing_is_a_failure(monkeypatch, tmp_path):
    class SilentVoice:
        config = type("C", (), {"sample_rate": 22050})()

        def synthesize(self, text, syn_config=None):
            return iter(())

    monkeypatch.setattr(tts, "_load_piper_voice", lambda: (SilentVoice(), None))
    engine = tts.PiperEngine(semaphore=asyncio.Semaphore(1))
    with pytest.raises(tts.TTSError):
        asyncio.run(engine.synthesize("Hello.", tmp_path / "p.mp3"))


def test_a_deleted_document_stops_the_local_engine_too(monkeypatch, tmp_path):
    """Deletion is noticed as the concurrency slot is granted, not at task
    start — with a whole book queued, a check at task start would pass for
    everything in the first instant and never look again. This reproduces the
    real race: the folder goes away while the page is still waiting in line."""
    monkeypatch.setattr(
        tts, "_load_piper_voice",
        lambda: pytest.fail("a deleted document should not load the model"),
    )
    out_dir = tmp_path / "doc"
    out_dir.mkdir()
    semaphore = asyncio.Semaphore(1)

    async def go():
        engine = tts.PiperEngine(semaphore=semaphore)
        await semaphore.acquire()  # hold the only slot
        task = asyncio.create_task(engine.synthesize("Hello.", out_dir / "p.mp3"))
        await asyncio.sleep(0)     # let it queue on the semaphore
        out_dir.rmdir()            # the document is deleted meanwhile
        semaphore.release()
        return await task

    with pytest.raises(tts.TTSError, match="no longer exists"):
        asyncio.run(go())


def test_pages_racing_the_switch_do_not_each_redecide(tmp_path):
    """Pages are narrated concurrently, so during an outage a whole book's
    worth of them fail at once and all arrive at the switch together. The
    first few decide; the rest must quietly follow rather than each announcing
    a fallback with an ever-higher failure count — which is what a 300-page
    book's logs looked like before this guard."""
    threshold = tts.config.LOCAL_TTS_FALLBACK_AFTER
    pages = 40
    engine, edge, local = build([SERVICE_SIDE] * pages, page_count=pages)

    async def go():
        # All at once, which is how the pipeline actually runs them.
        return await asyncio.gather(
            *[
                engine.synthesize("Some text.", tmp_path / f"page_{i}.mp3")
                for i in range(pages)
            ],
            return_exceptions=True,
        )

    results = asyncio.run(go())

    failed = [r for r in results if isinstance(r, BaseException)]
    rendered = [r for r in results if not isinstance(r, BaseException)]
    # Only the pages that genuinely established the outage are spent.
    assert len(failed) == threshold - 1
    assert len(rendered) == pages - threshold + 1
    assert engine.using_local is True
    # The count stops at the threshold instead of climbing to 40.
    assert engine._service_failures == threshold


def test_an_interrupted_model_download_leaves_nothing_behind(tmp_path):
    """A truncated .onnx at the path everything checks for would never be
    re-fetched — it exists and is non-empty — so the fallback would be
    permanently broken by exactly the kind of interruption it exists to
    survive. Nothing moves into place until the whole download has arrived."""
    model_dir = tmp_path / "piper"

    def half_a_download(name, into):
        (Path(into) / f"{name}.onnx").write_bytes(b"truncated")
        raise OSError("connection dropped")

    with pytest.raises(OSError):
        tts._download_voice(half_a_download, "en_US-lessac-medium", model_dir)

    # No partial model, and no staging directory left lying around.
    assert list(model_dir.glob("*.onnx")) == []
    assert list(model_dir.glob(".*partial")) == []


def test_a_complete_model_download_is_moved_into_place(tmp_path):
    model_dir = tmp_path / "piper"

    def whole_download(name, into):
        (Path(into) / f"{name}.onnx").write_bytes(b"weights")
        (Path(into) / f"{name}.onnx.json").write_text("{}")

    tts._download_voice(whole_download, "en_US-lessac-medium", model_dir)

    assert (model_dir / "en_US-lessac-medium.onnx").read_bytes() == b"weights"
    assert (model_dir / "en_US-lessac-medium.onnx.json").exists()
    assert list(model_dir.glob(".*partial")) == []
