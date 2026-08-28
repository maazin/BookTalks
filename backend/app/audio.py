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
SAMPLE_RATE = 24000
BITRATE = "48k"


class AudioError(Exception):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AudioError(f"{tool} is not installed or not on PATH.")
    return path


def _run(args: List[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
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
