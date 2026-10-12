"""Point edge-tts at the synthesis endpoint Microsoft actually still serves.

In October 2026 Microsoft started refusing speech synthesis on the host
edge-tts has always used, `speech.platform.bing.com/.../readaloud/edge/v1`,
with a 403 on the websocket handshake. Nothing about the request is wrong and
no amount of retrying helps: Edge 155 moved synthesis to
`api.msedgeservices.com`, which takes the same trusted client token under a
different parameter name, and the old host is being retired. The symptom is
total — every page of every book fails in seconds with
"edge-tts failed after 3 attempts: 403" — so without this the app cannot
convert anything at all.

edge-tts 7.2.8 (the newest release) still ships the dead URL; the upstream
fix is open but unreleased (rany2/edge-tts#490, PR #491). This patch is
deliberately conditional: once a release carries the new host, the check
below stops matching and this becomes a no-op, so bumping the pin is all
that's needed to retire it.

Only synthesis moved. The voice list is still served by the legacy host, so
`VOICE_LIST` is left alone — see voices.py.
"""
import logging

log = logging.getLogger(__name__)

# The host that now answers 403. Matched as a substring so a patched or
# newer edge-tts that already points elsewhere is left untouched.
_RETIRED_HOST = "speech.platform.bing.com"

_WSS_URL = (
    "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
    "?Ocp-Apim-Subscription-Key={token}"
)


def apply() -> bool:
    """Repoint edge-tts's synthesis websocket. True if it needed patching.

    Safe to call more than once.
    """
    try:
        from edge_tts import communicate, constants
    except ImportError:  # pragma: no cover - edge-tts is a hard dependency
        return False

    # communicate.py does `from .constants import WSS_URL`, binding the value
    # at import time, so rewriting constants.WSS_URL alone would change
    # nothing. That module-level name is the one the connection actually
    # reads; constants is updated too so both agree.
    current = getattr(communicate, "WSS_URL", "")
    if _RETIRED_HOST not in current:
        return False

    token = getattr(constants, "TRUSTED_CLIENT_TOKEN", "")
    if not token:
        return False

    patched = _WSS_URL.format(token=token)
    communicate.WSS_URL = patched
    constants.WSS_URL = patched
    log.info("Patched edge-tts synthesis endpoint to api.msedgeservices.com")
    return True
