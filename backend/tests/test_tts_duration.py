"""_render_chunk reads duration from edge-tts's own timing metadata instead
of shelling out to ffprobe — one fewer subprocess per page. These tests
stub edge_tts.Communicate entirely, so they need no network and no ffmpeg.
"""
import asyncio
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tts  # noqa: E402


class _FakeCommunicate:
    """Stands in for edge_tts.Communicate, yielding canned stream messages."""

    def __init__(self, messages: List[dict]):
        self._messages = messages

    async def stream(self):
        for message in self._messages:
            yield message


def _install_fake(monkeypatch, messages: List[dict]) -> None:
    monkeypatch.setattr(
        tts.edge_tts, "Communicate", lambda *a, **k: _FakeCommunicate(messages)
    )


def test_duration_comes_from_the_last_boundary_not_a_subprocess(monkeypatch, tmp_path):
    # 3 ticks/sec here would be absurd for real audio, but the point is only
    # to check the arithmetic: (offset + duration) / 10_000_000 -> seconds.
    messages = [
        {"type": "audio", "data": b"fake-mp3-bytes"},
        {"type": "SentenceBoundary", "offset": 0, "duration": 20_000_000},
        {"type": "SentenceBoundary", "offset": 20_000_000, "duration": 15_500_000},
    ]
    _install_fake(monkeypatch, messages)

    out = tmp_path / "page_1.mp3"
    engine = tts.EdgeTTSEngine(semaphore=asyncio.Semaphore(5))
    duration = asyncio.run(engine._render_chunk("hello world", out))

    assert duration == pytest.approx(3.55)  # (20_000_000 + 15_500_000) / 10_000_000
    assert out.read_bytes() == b"fake-mp3-bytes"


def test_falls_back_to_none_when_no_boundary_metadata_arrives(monkeypatch, tmp_path):
    # synthesize() is what falls back to ffprobe when this happens — verified
    # by test_synthesize_falls_back_to_ffprobe_without_metadata below.
    _install_fake(monkeypatch, [{"type": "audio", "data": b"fake-mp3-bytes"}])

    out = tmp_path / "page_1.mp3"
    engine = tts.EdgeTTSEngine(semaphore=asyncio.Semaphore(5))
    duration = asyncio.run(engine._render_chunk("hello world", out))

    assert duration is None
    assert out.read_bytes() == b"fake-mp3-bytes"


def test_synthesize_falls_back_to_ffprobe_without_metadata(monkeypatch, tmp_path):
    _install_fake(monkeypatch, [{"type": "audio", "data": b"fake-mp3-bytes"}])
    monkeypatch.setattr(tts.audio, "duration_sec", lambda path: 7.25)

    out = tmp_path / "page_1.mp3"
    engine = tts.EdgeTTSEngine(semaphore=asyncio.Semaphore(5))
    duration = asyncio.run(engine.synthesize("hello world", out))

    assert duration == 7.25


def test_empty_response_is_still_treated_as_a_failure(monkeypatch, tmp_path):
    _install_fake(monkeypatch, [])  # no audio at all
    monkeypatch.setattr(tts.config, "TTS_MAX_RETRIES", 1)

    out = tmp_path / "page_1.mp3"
    engine = tts.EdgeTTSEngine(semaphore=asyncio.Semaphore(5))
    with pytest.raises(tts.TTSError):
        asyncio.run(engine._render_chunk("hello world", out))
    assert not out.exists()
