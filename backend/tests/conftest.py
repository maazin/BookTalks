import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient backed by a throwaway data directory and database."""
    os.environ["BOOKTALKS_DATA_DIR"] = str(tmp_path)
    os.environ["BOOKTALKS_DB_PATH"] = str(tmp_path / "test.db")

    for module in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[module]

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
