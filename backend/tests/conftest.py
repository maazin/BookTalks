import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# A small stand-in for edge-tts's real ~320-voice list (fetched over the
# network). Every test that touches voices — including indirectly, via
# uploading a document — uses this instead, so the suite stays network-free.
STUB_VOICES = [
    {
        "short_name": "en-US-AriaNeural",
        "display_name": "Aria",
        "gender": "Female",
        "locale": "en-US",
        "locale_name": "English (United States)",
    },
    {
        "short_name": "en-GB-RyanNeural",
        "display_name": "Ryan",
        "gender": "Male",
        "locale": "en-GB",
        "locale_name": "English (United Kingdom)",
    },
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient backed by a throwaway data directory and database."""
    os.environ["BOOKTALKS_DATA_DIR"] = str(tmp_path)
    os.environ["BOOKTALKS_DB_PATH"] = str(tmp_path / "test.db")

    for module in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[module]

    from fastapi.testclient import TestClient

    from app import voices
    from app.main import app

    async def fake_list_voices():
        return STUB_VOICES

    monkeypatch.setattr(voices, "list_voices", fake_list_voices)

    with TestClient(app) as test_client:
        yield test_client
