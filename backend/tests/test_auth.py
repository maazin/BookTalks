"""Auth is disabled by default (test_api.py exercises that path); these tests
turn it on and check the gate itself."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def protected_client(tmp_path, monkeypatch):
    os.environ["BOOKTALKS_DATA_DIR"] = str(tmp_path)
    os.environ["BOOKTALKS_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["BOOKTALKS_PASSWORD"] = "correct-horse"
    os.environ["BOOKTALKS_SESSION_SECRET"] = "test-secret"

    for module in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[module]

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    del os.environ["BOOKTALKS_PASSWORD"]
    del os.environ["BOOKTALKS_SESSION_SECRET"]


def test_status_reports_auth_is_required(protected_client):
    body = protected_client.get("/api/auth/status").json()
    assert body == {"required": True, "authenticated": False}


def test_protected_routes_reject_anonymous_requests(protected_client):
    for method, path in [
        ("GET", "/api/documents"),
        ("GET", "/api/documents/1"),
        ("GET", "/api/documents/1/pages"),
        ("GET", "/api/documents/1/audio"),
        ("GET", "/api/documents/1/playback"),
        ("PUT", "/api/documents/1/playback"),
        ("DELETE", "/api/documents/1"),
        ("POST", "/api/documents"),
    ]:
        response = protected_client.request(method, path)
        assert response.status_code == 401, f"{method} {path} was not gated"


def test_health_stays_open_without_a_session(protected_client):
    assert protected_client.get("/api/health").status_code == 200


def test_wrong_password_is_rejected(protected_client):
    response = protected_client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert protected_client.get("/api/documents").status_code == 401


def test_correct_password_unlocks_the_session(protected_client):
    response = protected_client.post(
        "/api/auth/login", json={"password": "correct-horse"}
    )
    assert response.status_code == 200
    assert protected_client.cookies.get("booktalks_session") is not None

    assert protected_client.get("/api/documents").status_code == 200
    assert protected_client.get("/api/auth/status").json() == {
        "required": True,
        "authenticated": True,
    }


def test_logout_ends_the_session(protected_client):
    protected_client.post("/api/auth/login", json={"password": "correct-horse"})
    assert protected_client.get("/api/documents").status_code == 200

    protected_client.post("/api/auth/logout")
    assert protected_client.get("/api/documents").status_code == 401


def test_logout_revokes_the_token_itself_not_just_the_browsers_copy(protected_client):
    """A signed cookie can't be deleted server-side — logout has to move the
    validity cutoff instead. Replaying the pre-logout cookie must still fail,
    otherwise "logout" only ever removed the cookie from one browser."""
    protected_client.post("/api/auth/login", json={"password": "correct-horse"})
    stolen_cookie = protected_client.cookies.get("booktalks_session")
    assert stolen_cookie

    protected_client.post("/api/auth/logout")

    protected_client.cookies.set("booktalks_session", stolen_cookie)
    assert protected_client.get("/api/documents").status_code == 401


def test_repeated_wrong_passwords_get_rate_limited(protected_client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "AUTH_RATE_LIMIT", 3)

    for _ in range(3):
        response = protected_client.post("/api/auth/login", json={"password": "x"})
        assert response.status_code == 401

    limited = protected_client.post("/api/auth/login", json={"password": "x"})
    assert limited.status_code == 429

    # Even the right password is refused while rate limited.
    limited = protected_client.post(
        "/api/auth/login", json={"password": "correct-horse"}
    )
    assert limited.status_code == 429


def test_a_forged_cookie_is_rejected(protected_client):
    protected_client.cookies.set("booktalks_session", "9999999999.deadbeef")
    assert protected_client.get("/api/documents").status_code == 401


def test_an_expired_cookie_is_rejected(protected_client):
    from app import auth

    stale = auth._sign(auth._generation, 1)  # correct generation, expired in 1970
    protected_client.cookies.set("booktalks_session", stale)
    assert protected_client.get("/api/documents").status_code == 401
