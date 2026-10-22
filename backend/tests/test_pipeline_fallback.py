"""The pipeline's half of the fallback: what gets recorded, and the pages that
a mid-document engine switch would otherwise lose.

Network-free and ffmpeg-free — the engine is a stub and durations are made up.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def app_modules(tmp_path):
    """The app, pointed at a throwaway data directory and database."""
    os.environ["BOOKTALKS_DATA_DIR"] = str(tmp_path)
    os.environ["BOOKTALKS_DB_PATH"] = str(tmp_path / "test.db")
    for module in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[module]

    from app import db, pipeline, store, tts

    db.init_db()
    return pipeline, store, tts


class SwitchingEngine:
    """Fails service-side for the first `fail_first` pages, then succeeds —
    exactly the shape of a document that switched engines part-way."""

    def __init__(self, tts_module, fail_first: int):
        self._tts = tts_module
        self._remaining = fail_first
        self.using_local = False
        self.engines_used = set()
        self.calls = []

    async def synthesize(self, text: str, out_path: Path) -> float:
        self.calls.append(out_path.name)
        if self._remaining > 0:
            self._remaining -= 1
            self.using_local = True  # the switch has tripped
            raise self._tts.TTSError("edge-tts failed: HTTP 403")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"audio")
        self.engines_used.add(self._tts.ENGINE_LOCAL)
        return 2.0


def _document(store, pages):
    document_id = store.create_document("book.pdf", "en-US-AriaNeural")
    store.set_page_count(document_id, len(pages))
    store.insert_pages(document_id, pages)
    return document_id


def test_pages_lost_before_the_switch_are_narrated_locally(app_modules, tmp_path):
    """The pages that failed while edge-tts was still being trusted aren't
    broken pages — the service was broken. Leaving them out would mean a book
    that permanently opens with silence."""
    pipeline, store, tts = app_modules
    pages = [f"Page {i} text." for i in range(1, 6)]
    document_id = _document(store, pages)

    engine = SwitchingEngine(tts, fail_first=2)
    out_dir = tmp_path / "audio" / str(document_id)

    import unittest.mock
    with unittest.mock.patch.object(tts, "get_engine", lambda *a, **k: engine):
        durations, label = asyncio.run(
            pipeline._narrate_pages(document_id, pages, out_dir, "en-US-AriaNeural")
        )

    # Every page ends up narrated, including the two that failed first.
    assert sorted(durations) == [1, 2, 3, 4, 5]
    assert label == "piper"
    # Those two pages are no longer marked failed in the database either.
    statuses = {p["page_number"]: p["status"] for p in store.get_pages(document_id)}
    assert set(statuses.values()) == {"done"}


def test_the_engine_is_recorded_against_the_document(app_modules, tmp_path):
    pipeline, store, tts = app_modules
    pages = ["One.", "Two."]
    document_id = _document(store, pages)

    store.set_document_engine(document_id, "edge-tts+piper")
    assert store.get_document(document_id)["engine"] == "edge-tts+piper"


def test_a_document_narrated_before_the_fallback_existed_has_no_engine(app_modules):
    """NULL, not a guess at edge-tts: the column was added later and "we don't
    know" is the honest answer for everything that predates it."""
    pipeline, store, tts = app_modules
    document_id = _document(store, ["One."])
    assert store.get_document(document_id)["engine"] is None


def test_without_a_fallback_the_engine_still_names_itself(app_modules, tmp_path):
    """A plain edge-tts engine has no engines_used to report, but a document
    it narrated should still say who narrated it."""
    pipeline, store, tts = app_modules
    pages = ["One.", "Two."]
    document_id = _document(store, pages)

    class PlainEdge:
        label = tts.ENGINE_EDGE

        async def synthesize(self, text, out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"audio")
            return 1.0

    import unittest.mock
    with unittest.mock.patch.object(tts, "get_engine", lambda *a, **k: PlainEdge()):
        durations, label = asyncio.run(
            pipeline._narrate_pages(
                document_id, pages, tmp_path / "a", "en-US-AriaNeural"
            )
        )
    assert sorted(durations) == [1, 2]
    assert label == "edge-tts"


def test_nothing_narrated_means_no_engine_is_claimed(app_modules, tmp_path):
    """A document that failed outright must not be labelled as though some
    engine successfully spoke it."""
    pipeline, store, tts = app_modules
    pages = ["One.", "Two."]
    document_id = _document(store, pages)

    class AlwaysFails:
        label = tts.ENGINE_EDGE

        async def synthesize(self, text, out_path):
            raise tts.TTSError("edge-tts returned an empty audio file")

    import unittest.mock
    with unittest.mock.patch.object(tts, "get_engine", lambda *a, **k: AlwaysFails()):
        durations, label = asyncio.run(
            pipeline._narrate_pages(
                document_id, pages, tmp_path / "a", "en-US-AriaNeural"
            )
        )
    assert durations == {}
    assert label is None
