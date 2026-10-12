"""Audio helpers built directly on ffmpeg/ffprobe.

Durations come from ffprobe (a header read, not a full decode) and pages are
joined with the concat demuxer in stream-copy mode. Nothing is re-encoded, so
assembling a 300-page book takes about as long as copying the file — and the
final duration is exactly the sum of the parts, which is what keeps the
jump-to-page offsets honest.
"""
import json
import shutil
import subprocess
from pathlib import Path
from typing import List

# Matches what edge-tts returns, so silence splices in without a format change.
# The local engine's output is resampled to this too — see encode_pcm_to_mp3,
# and concat_mp3s below for why every part has to agree.
SAMPLE_RATE = 24000
BITRATE = "48k"


class AudioError(Exception):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AudioError(f"{tool} is not installed or not on PATH.")
    return path


def _run(args: List[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    # text=True only when nothing is piped in: stdin here is raw PCM, and the
    # two can't both be decoded as text.
    result = subprocess.run(
        args, input=stdin, capture_output=True, text=stdin is None
    )
    if result.returncode != 0:
        stderr = result.stderr if stdin is None else result.stderr.decode(
            "utf-8", "replace"
        )
        tail = (stderr or "").strip().splitlines()[-3:]
        raise AudioError(f"{args[0]} failed: {' '.join(tail)}")
    return result


def duration_sec(path: Path) -> float:
    """Duration in seconds, read from the container without decoding audio."""
    result = _run(
        [
            _require("ffprobe"), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    try:
        value = json.loads(result.stdout)["format"]["duration"]
        return float(value)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AudioError(f"Could not read the duration of {path.name}") from exc


def write_silence(path: Path, seconds: float = 0.4) -> float:
    """Write a short silent mp3 — a blank page still needs a slot on the
    timeline so page offsets stay aligned with the audio."""
    _run(
        [
            _require("ffmpeg"), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
            "-t", f"{max(seconds, 0.1):.3f}",
            "-c:a", "libmp3lame", "-b:a", BITRATE,
            # No Xing header: this file will be spliced into a larger one, and
            # a header frame there would decode as an extra sliver of silence.
            "-write_xing", "0",
            str(path),
        ]
    )
    return duration_sec(path)


def encode_pcm_to_mp3(
    pcm: bytes, out_path: Path, source_rate: int, xing: bool = False
) -> float:
    """Encode signed 16-bit mono PCM to an mp3 in the app's one audio format.

    The local TTS engine produces raw samples at whatever rate its model was
    trained on — 22050 Hz for Piper's medium voices, where edge-tts returns
    24000 Hz. That difference cannot be left in: concat_mp3s joins pages with
    `-c copy`, so the parts are never decoded and a rate change part-way
    through would make the rest of the book play at the wrong pitch and speed.
    A fallback can trip mid-document, which is exactly when one book ends up
    holding pages from both engines, so ffmpeg resamples to SAMPLE_RATE here
    and every page leaves this module identical in format regardless of which
    engine spoke it.

    `xing=False` by default for the same reason write_silence does it: a page
    is a part of something bigger, and a header frame there decodes as an
    extra sliver of silence at every join.
    """
    _run(
        [
            _require("ffmpeg"), "-y", "-loglevel", "error",
            "-f", "s16le", "-ar", str(source_rate), "-ac", "1", "-i", "pipe:0",
            "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", BITRATE,
            "-write_xing", "1" if xing else "0",
            str(out_path),
        ],
        stdin=pcm,
    )
    return duration_sec(out_path)


def concat_mp3s(parts: List[Path], out_path: Path, xing: bool = True) -> float:
    """Concatenate mp3s in order without re-encoding. Returns the duration.

    `xing=False` for intermediate files that will themselves be spliced into
    something bigger; the final audiobook keeps its header so players know the
    exact duration up front.
    """
    if not parts:
        raise AudioError("Nothing to concatenate")

    listing = out_path.with_suffix(".concat.txt")
    listing.write_text(
        "".join(f"file '{part.resolve().as_posix()}'\n" for part in parts),
        encoding="utf-8",
    )
    try:
        _run(
            [
                _require("ffmpeg"), "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy", "-write_xing", "1" if xing else "0",
                str(out_path),
            ]
        )
    finally:
        listing.unlink(missing_ok=True)

    return duration_sec(out_path)
