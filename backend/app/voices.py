"""The list of narration voices edge-tts offers, cached in memory.

edge_tts.list_voices() is a network call to Microsoft's service — worth
caching rather than paying for on every picker open, and worth having a
fallback for if that network call ever fails.
"""
import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

import edge_tts

from . import config

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 24 * 3600
_cache: Optional[List[Dict[str, Any]]] = None
_cache_at: float = 0.0
_lock = asyncio.Lock()

# "en-US-AriaNeural" -> "Aria". Strips the locale prefix and the "Neural" /
# "MultilingualNeural" suffix edge-tts appends to every voice name, leaving
# just the part a person would recognize.
_NAME_RE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-(.+?)(?:Multilingual)?Neural\d*$")


def _display_name(short_name: str) -> str:
    match = _NAME_RE.match(short_name)
    return match.group(1) if match else short_name


def _simplify(raw_voices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    simplified = [
        {
            "short_name": v["ShortName"],
            "display_name": _display_name(v["ShortName"]),
            "gender": v["Gender"],
            "locale": v["Locale"],
            "locale_name": v["LocaleName"],
        }
        for v in raw_voices
    ]
    # English first (most people's documents are in English), then
    # alphabetically by language and name within that.
    simplified.sort(
        key=lambda v: (
            0 if v["locale"].startswith("en") else 1,
            v["locale_name"],
            v["display_name"],
        )
    )
    return simplified


# A handful of well-known voices to fall back on if the network call to
# Microsoft's voice list ever fails — better than an empty picker.
_FALLBACK = [
    {"short_name": "en-US-AriaNeural", "display_name": "Aria", "gender": "Female", "locale": "en-US", "locale_name": "English (United States)"},
    {"short_name": "en-US-GuyNeural", "display_name": "Guy", "gender": "Male", "locale": "en-US", "locale_name": "English (United States)"},
    {"short_name": "en-GB-SoniaNeural", "display_name": "Sonia", "gender": "Female", "locale": "en-GB", "locale_name": "English (United Kingdom)"},
    {"short_name": "en-GB-RyanNeural", "display_name": "Ryan", "gender": "Male", "locale": "en-GB", "locale_name": "English (United Kingdom)"},
    {"short_name": "en-AU-NatashaNeural", "display_name": "Natasha", "gender": "Female", "locale": "en-AU", "locale_name": "English (Australia)"},
    {"short_name": "en-IN-NeerjaNeural", "display_name": "Neerja", "gender": "Female", "locale": "en-IN", "locale_name": "English (India)"},
]


async def list_voices() -> List[Dict[str, Any]]:
    global _cache, _cache_at
    if _cache is not None and time.time() - _cache_at < _CACHE_TTL_SEC:
        return _cache

    async with _lock:
        # Another request may have refreshed it while this one waited.
        if _cache is not None and time.time() - _cache_at < _CACHE_TTL_SEC:
            return _cache
        try:
            raw = await edge_tts.list_voices()
            _cache = _simplify(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch the edge-tts voice list: %s", exc)
            _cache = _cache or _FALLBACK
        _cache_at = time.time()
        return _cache


async def is_known_voice(short_name: str) -> bool:
    voices = await list_voices()
    return any(v["short_name"] == short_name for v in voices)


def default_voice() -> str:
    return config.TTS_VOICE
