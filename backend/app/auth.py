"""One shared password, gating the API when BOOKTALKS_PASSWORD is set.

There's no user table — this is still the single-user tool the PRD describes,
just reachable from somewhere other than localhost now. The session is a
signed, stateless cookie (expiry + HMAC), so there's nothing to persist and
nothing that breaks when the process restarts.
"""
import hashlib
import hmac
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import config

COOKIE_NAME = "booktalks_session"
router = APIRouter(prefix="/api/auth", tags=["auth"])


def auth_required() -> bool:
    return config.AUTH_PASSWORD is not None


# A stateless signed cookie can't be individually revoked — there's nothing
# server-side to delete. What it can carry instead is the session "generation"
# it was issued under; logout (and a process restart) bumps the generation,
# which invalidates every token issued under the old one in one step. A
# counter sidesteps the precision problems a wall-clock cutoff would have —
# two events a microsecond apart still compare correctly, with no risk of a
# token's issue time rounding to the same tick as the cutoff that should
# postdate it. Right granularity for a single-user, single-session tool, and
# it needs no storage of its own.
_generation = 0


def _sign(generation: int, expires_at: int) -> str:
    mac = hmac.new(
        config.AUTH_SESSION_SECRET.encode(),
        f"{generation}.{expires_at}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{generation}.{expires_at}.{mac}"


def _verify(token: str) -> bool:
    try:
        generation_str, expires_at_str, mac = token.split(".", 2)
        generation, expires_at = int(generation_str), int(expires_at_str)
    except (ValueError, AttributeError):
        return False
    if expires_at < time.time() or generation != _generation:
        return False
    expected = hmac.new(
        config.AUTH_SESSION_SECRET.encode(),
        f"{generation_str}.{expires_at_str}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(mac, expected)


def _set_session_cookie(response: Response) -> None:
    max_age = config.AUTH_SESSION_DAYS * 86400
    response.set_cookie(
        COOKIE_NAME,
        _sign(_generation, int(time.time()) + max_age),
        max_age=max_age,
        httponly=True,
        secure=config.SECURE_COOKIES,
        samesite="lax",
        path="/",
    )


def _revoke_all_sessions() -> None:
    global _generation
    _generation += 1


def is_authenticated(request: Request) -> bool:
    if not auth_required():
        return True
    token = request.cookies.get(COOKIE_NAME)
    return bool(token) and _verify(token)


def require_auth(request: Request) -> None:
    """FastAPI dependency — attach to any route that needs the gate."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Sign in required.")


# --- brute-force guard -------------------------------------------------------
# In-memory and per-process: fine for a single-instance deployment, and it
# resets on restart rather than needing its own storage.
_attempts: Dict[str, Deque[float]] = defaultdict(deque)


def _rate_limited(client_ip: str) -> bool:
    now = time.time()
    window = _attempts[client_ip]
    while window and now - window[0] > config.AUTH_RATE_WINDOW_SEC:
        window.popleft()
    return len(window) >= config.AUTH_RATE_LIMIT


def _record_attempt(client_ip: str) -> None:
    _attempts[client_ip].append(time.time())


class LoginIn(BaseModel):
    password: str


@router.get("/status")
def status(request: Request) -> Dict[str, bool]:
    return {"required": auth_required(), "authenticated": is_authenticated(request)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> Dict[str, bool]:
    if not auth_required():
        return {"ok": True}

    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        raise HTTPException(
            status_code=429, detail="Too many attempts. Try again in a few minutes."
        )

    if not hmac.compare_digest(body.password, config.AUTH_PASSWORD):
        _record_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Wrong password.")

    _set_session_cookie(response)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> Dict[str, bool]:
    # Invalidates every outstanding session, not just this browser's — there's
    # only ever meant to be one, and a stolen cookie shouldn't survive logout.
    _revoke_all_sessions()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
