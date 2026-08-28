"""Voice picker: listing, per-document selection, and validation.

Uses the STUB_VOICES list from conftest — see there for why: the real list
comes from a network call to edge-tts, and this suite stays network-free.
"""
import io

import fitz
import pytest

from conftest import STUB_VOICES


def make_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Some readable text for a test page.")
    return doc.tobytes()


@pytest.fixture()
def no_processing(monkeypatch):
    from app import pipeline

    async def noop(document_id):
        return None

    monkeypatch.setattr(pipeline, "process_document", noop)


def upload(client, voice=None):
    data = {"voice": voice} if voice else {}
    return client.post(
        "/api/documents",
        files={"file": ("book.pdf", io.BytesIO(make_pdf()), "application/pdf")},
        data=data,
    )


def test_lists_voices_with_a_default(client):
    body = client.get("/api/voices").json()
    assert body["default"] == "en-US-AriaNeural"
    assert body["voices"] == STUB_VOICES


def test_upload_with_a_chosen_voice_persists_it(client, no_processing):
    response = upload(client, voice="en-GB-RyanNeural")
    assert response.status_code == 201
    assert response.json()["voice"] == "en-GB-RyanNeural"

    document_id = response.json()["id"]
    detail = client.get(f"/api/documents/{document_id}").json()
    assert detail["voice"] == "en-GB-RyanNeural"


def test_upload_without_a_voice_falls_back_to_the_default(client, no_processing):
    response = upload(client)
    assert response.status_code == 201
    assert response.json()["voice"] == "en-US-AriaNeural"


def test_upload_rejects_an_unknown_voice(client, no_processing):
    response = upload(client, voice="xx-XX-NotARealVoice")
    assert response.status_code == 400
    assert "voice" in response.json()["detail"].lower()


def test_voice_is_fixed_once_chosen_pipeline_reads_the_stored_value(
    client, monkeypatch
):
    """The pipeline must narrate with the document's own voice, not whatever
    the server's default happens to be — this is what makes per-document
    selection actually work end to end, not just get accepted at upload."""
    from app import pipeline

    seen = {}

    async def fake_narrate_pages(document_id, pages, out_dir, voice):
        seen["voice"] = voice
        return {}

    # extract_and_clean runs via asyncio.to_thread — it's a sync function, not
    # a coroutine, and must stay one here.
    def fake_extract_and_clean(path):
        return ["Some text."]

    monkeypatch.setattr(pipeline, "_narrate_pages", fake_narrate_pages)
    monkeypatch.setattr(pipeline.pdf_text, "extract_and_clean", fake_extract_and_clean)

    document_id = upload(client, voice="en-GB-RyanNeural").json()["id"]

    import asyncio

    asyncio.run(pipeline.process_document(document_id))

    assert seen["voice"] == "en-GB-RyanNeural"
