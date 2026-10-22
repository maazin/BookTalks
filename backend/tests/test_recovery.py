"""Exercise persisted progress, bounded batches, retries, and restart recovery."""
import asyncio

import fitz
from fastapi.testclient import TestClient


def prepare(pages=5):
    from app import config, pipeline, store
    document_id = store.create_document("large.pdf", config.TTS_VOICE)
    store.set_page_count(document_id, pages)
    with fitz.open() as pdf:
        for index in range(pages):
            page = pdf.new_page()
            page.insert_text((72, 100), f"This is a sufficiently long readable paragraph on page {index + 1}.")
        pdf.save(pipeline.document_pdf_path(document_id))
    return document_id


def fake_audio(monkeypatch, fail=None):
    from app import pipeline
    calls = []
    active = 0
    peak = 0

    class Engine:
        async def synthesize(self, text, path):
            nonlocal active, peak
            index = int(path.stem.split("_")[1])
            calls.append(index)
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(0)
                if index == fail:
                    raise RuntimeError("temporary failure")
                path.write_bytes(b"audio")
                return 1.0
            finally:
                active -= 1

    def concat(parts, target):
        target.write_bytes(b"".join(p.read_bytes() for p in parts))
        return float(len(parts))

    monkeypatch.setattr(pipeline.tts, "get_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(pipeline.audio, "concat_mp3s", concat)
    return calls, lambda: peak


def test_small_parts_and_retry_only_missing_page(client, monkeypatch):
    from app import config, pipeline, store
    monkeypatch.setattr(config, "PDF_PART_PAGES", 2)
    doc_id = prepare()
    calls, peak = fake_audio(monkeypatch, fail=3)
    asyncio.run(pipeline.process_document(doc_id))
    assert peak() <= 2
    assert store.get_document(doc_id)["status"] == "failed"
    assert not pipeline.full_audio_path(doc_id).exists()
    parts = client.get(f"/api/documents/{doc_id}/parts").json()
    assert [(p["start_page"], p["end_page"]) for p in parts] == [(1, 2), (5, 5)]
    assert client.get(parts[0]["url"]).content == b"audioaudio"
    calls, _ = fake_audio(monkeypatch)
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 202
    assert calls == [3]
    assert store.get_document(doc_id)["status"] == "ready"
    assert len(client.get(f"/api/documents/{doc_id}").json()["parts"]) == 3
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 409
    assert not list(pipeline.document_audio_dir(doc_id).glob("page_*.mp3"))


def test_startup_resumes_checkpoint_and_preserves_playback(client, monkeypatch):
    from app import pipeline, store
    from app.main import app
    doc_id = prepare(2)
    store.insert_pages(doc_id, ["First page", "Second page"])
    folder = pipeline.document_audio_dir(doc_id)
    folder.mkdir()
    (folder / "page_1.mp3").write_bytes(b"audio")
    store.mark_page_done(doc_id, 1, str(folder / "page_1.mp3"), 1.0)
    store.set_document_status(doc_id, "generating_audio")
    store.upsert_playback(doc_id, 0.5, 1.25)
    calls, _ = fake_audio(monkeypatch)
    with TestClient(app) as restarted:
        # A request also gives the scheduled recovery task an event-loop turn.
        import time
        for _ in range(100):
            if restarted.get(f"/api/documents/{doc_id}").json()["status"] == "ready":
                break
            time.sleep(0.01)
        assert store.get_document(doc_id)["status"] == "ready"
    assert calls == [2]
    assert store.get_playback(doc_id)["position_sec"] == 0.5


def test_missing_checkpoint_audio_is_regenerated(client, monkeypatch):
    from app import pipeline, store
    doc_id = prepare(2)
    store.insert_pages(doc_id, ["First page", "Second page"])
    store.mark_page_done(doc_id, 1, "missing.mp3", 1.0)
    calls, _ = fake_audio(monkeypatch)
    asyncio.run(pipeline.process_document(doc_id))
    assert sorted(calls) == [1, 2]


def test_page_insert_does_not_destroy_checkpoints(client):
    from app import store
    doc_id = prepare(1)
    store.insert_pages(doc_id, ["text"])
    store.mark_page_done(doc_id, 1, "page.mp3", 2.0)
    store.insert_pages(doc_id, ["text"])
    assert store.get_pages(doc_id)[0]["status"] == "done"


def test_retry_claim_is_atomic_and_missing_pdf_is_reported(client):
    from app import store, pipeline
    doc_id = prepare(1)
    store.set_document_status(doc_id, "failed")
    assert store.queue_retry(doc_id)
    assert not store.queue_retry(doc_id)
    store.set_document_status(doc_id, "failed")
    pipeline.document_pdf_path(doc_id).unlink()
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 409


def test_cancelled_conversion_drains_page_tasks(client, monkeypatch):
    from app import pipeline, store
    doc_id = prepare(2)
    started = asyncio.Event()
    stopped = []

    class Engine:
        async def synthesize(self, text, path):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.append(path.name)

    monkeypatch.setattr(pipeline.tts, "get_engine", lambda *a, **kw: Engine())

    async def interrupt():
        task = asyncio.create_task(pipeline.process_document(doc_id))
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(interrupt())
    assert len(stopped) == 2
    assert store.get_document(doc_id)["status"] == "generating_audio"
    assert all(p["status"] == "pending" for p in store.get_pages(doc_id))


def test_real_audio_parts_are_valid(client, monkeypatch):
    import shutil
    import pytest
    from app import audio, config, pipeline, store
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required")
    monkeypatch.setattr(config, "PDF_PART_PAGES", 2)
    doc_id = prepare(3)

    class Engine:
        async def synthesize(self, text, path):
            return await asyncio.to_thread(audio.write_silence, path, 0.2)

    monkeypatch.setattr(pipeline.tts, "get_engine", lambda *a, **kw: Engine())
    asyncio.run(pipeline.process_document(doc_id))
    assert store.get_document(doc_id)["status"] == "ready"
    for path in pipeline.document_audio_dir(doc_id).glob("*.mp3"):
        assert audio.duration_sec(path) > 0
    assert len(client.get(f"/api/documents/{doc_id}/parts").json()) == 2
