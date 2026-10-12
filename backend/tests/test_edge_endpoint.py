"""The synthesis endpoint patch — see app/edge_endpoint.

These assert the shape of the patch, not Microsoft's behaviour: a network
test would fail for reasons that have nothing to do with this code.
"""
from edge_tts import communicate, constants

from app import edge_endpoint
# Imported for its side effect, and asserted on below: importing tts is what
# applies the patch, so the app is never one import away from 403ing. Without
# this the tests below would pass or fail depending on whether some other
# test module happened to pull tts in first.
from app import tts  # noqa: F401


def test_synthesis_does_not_use_the_retired_host():
    """The whole point: nothing may still be pointed at the 403ing host.

    tts.py calls apply() at import, so by the time anything runs this must
    already hold.
    """
    assert edge_endpoint._RETIRED_HOST not in communicate.WSS_URL
    assert edge_endpoint._RETIRED_HOST not in constants.WSS_URL


def test_patch_keeps_the_trusted_client_token():
    """The new host takes the same token, under a different parameter name.
    Dropping it would turn every synthesis into a 401."""
    assert constants.TRUSTED_CLIENT_TOKEN in communicate.WSS_URL
    assert "Ocp-Apim-Subscription-Key" in communicate.WSS_URL


def test_apply_is_idempotent():
    """It runs at import of tts and may run again; a second call must not
    double-patch or report work it didn't do."""
    before = communicate.WSS_URL
    assert edge_endpoint.apply() is False
    assert communicate.WSS_URL == before


def test_apply_leaves_a_fixed_upstream_alone():
    """Once a released edge-tts ships the new host, this must stop acting —
    that's how the shim retires itself on a version bump."""
    upstream_fixed = "wss://example.invalid/tts/v1?Ocp-Apim-Subscription-Key=x"
    original = communicate.WSS_URL
    communicate.WSS_URL = upstream_fixed
    try:
        assert edge_endpoint.apply() is False
        assert communicate.WSS_URL == upstream_fixed
    finally:
        communicate.WSS_URL = original


def test_apply_patches_the_retired_host():
    """And the converse: when the dead URL is in place, it gets replaced."""
    original_comm = communicate.WSS_URL
    original_const = constants.WSS_URL
    communicate.WSS_URL = (
        "wss://speech.platform.bing.com/consumer/speech/synthesize"
        "/readaloud/edge/v1?TrustedClientToken=abc"
    )
    try:
        assert edge_endpoint.apply() is True
        assert "api.msedgeservices.com" in communicate.WSS_URL
        assert edge_endpoint._RETIRED_HOST not in communicate.WSS_URL
    finally:
        communicate.WSS_URL = original_comm
        constants.WSS_URL = original_const
