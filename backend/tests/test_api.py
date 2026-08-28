"""End-to-end API tests. TTS is stubbed out — no network, no audio rendering."""
import io

import fitz
import pytest


def make_pdf(pages=2, text="Hello there, this is a readable page of text."):
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"{text} Page {index + 1}.", fontsize=12)
    return doc.tobytes()


@pytest.fixture()
def no_processing(monkeypatch):
    """Keep uploads from kicking off real TTS work."""
    from app import pipeline

    async def noop(document_id):
        return None

    monkeypatch.setattr(pipeline, "process_document", noop)


def upload(client, name="book.pdf", data=None):
    return client.post(
        "/api/documents",
        files={
            "file": (
                name,
                io.BytesIO(make_pdf() if data is None else data),
                "application/pdf",
            )
        },
    )


def test_rejects_non_pdf_extension(client):
    response = client.post(
        "/api/documents", files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    )
    assert response.status_code == 400


def test_rejects_unreadable_pdf(client):
    response = upload(client, data=b"this is not a pdf")
    assert response.status_code == 400
    assert "readable" in response.json()["detail"]


def test_rejects_empty_file(client):
    response = upload(client, data=b"")
    assert response.status_code == 400


def test_upload_creates_a_pending_document(client, no_processing):
    response = upload(client, name="My Book.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"

    listing = client.get("/api/documents").json()
    assert [doc["filename"] for doc in listing] == ["My Book.pdf"]
    assert listing[0]["page_count"] == 2


def test_unknown_document_is_404(client):
    assert client.get("/api/documents/999").status_code == 404
    assert client.get("/api/documents/999/pages").status_code == 404
    assert client.delete("/api/documents/999").status_code == 404


def test_audio_is_409_until_ready(client, no_processing):
    document_id = upload(client).json()["id"]
    assert client.get(f"/api/documents/{document_id}/audio").status_code == 409


def test_playback_state_round_trips(client, no_processing):
    document_id = upload(client).json()["id"]

    assert client.get(f"/api/documents/{document_id}/playback").json() == {
        "position_sec": 0.0,
        "playback_rate": 1.0,
        "updated_at": None,
    }

    client.put(
        f"/api/documents/{document_id}/playback",
        json={"position_sec": 42.5, "playback_rate": 1.5},
    )
    saved = client.get(f"/api/documents/{document_id}/playback").json()
    assert saved["position_sec"] == 42.5
    assert saved["playback_rate"] == 1.5

    # Upserts, one row per document.
    client.put(
        f"/api/documents/{document_id}/playback",
        json={"position_sec": 60, "playback_rate": 2},
    )
    assert client.get(f"/api/documents/{document_id}/playback").json()["position_sec"] == 60


def test_playback_rejects_nonsense_values(client, no_processing):
    document_id = upload(client).json()["id"]
    response = client.put(
        f"/api/documents/{document_id}/playback",
        json={"position_sec": -5, "playback_rate": 1},
    )
    assert response.status_code == 422


def test_delete_removes_the_document_and_its_files(client, no_processing):
    from app import pipeline

    document_id = upload(client).json()["id"]
    assert pipeline.document_pdf_path(document_id).exists()

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.get(f"/api/documents/{document_id}").status_code == 404
    assert not pipeline.document_pdf_path(document_id).exists()


def test_audio_streams_with_range_support(client, no_processing):
    from app import pipeline, store

    document_id = upload(client).json()["id"]
    audio_path = pipeline.full_audio_path(document_id)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(bytes(range(256)) * 4)  # 1024 bytes of stand-in audio
    store.finish_document(document_id, 12.5)

    full = client.get(f"/api/documents/{document_id}/audio")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == 1024

    partial = client.get(
        f"/api/documents/{document_id}/audio", headers={"Range": "bytes=100-199"}
    )
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 100-199/1024"
    assert len(partial.content) == 100

    suffix = client.get(
        f"/api/documents/{document_id}/audio", headers={"Range": "bytes=-50"}
    )
    assert suffix.status_code == 206
    assert suffix.headers["content-range"] == "bytes 974-1023/1024"

    beyond = client.get(
        f"/api/documents/{document_id}/audio", headers={"Range": "bytes=5000-"}
    )
    assert beyond.status_code == 416
