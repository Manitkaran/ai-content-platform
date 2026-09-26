"""Media handling: accepted formats + FFmpeg audio extraction from video (M2.5, D8).

Video is handled by extracting its audio track with FFmpeg before transcription;
there is no visual processing (D8). Audio uploads skip extraction. Format is
detected from content (magic bytes) with the extension as a fallback hint, not
the sole signal (M2.5 acceptance).

FFmpeg is a system dependency declared in the Docker image (M0.2/M0.4). If it is
absent when a video needs extraction, that surfaces as a typed error and the job
becomes ``failed`` — the app stays up.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from enum import Enum

from app.model.contract import UnsupportedMediaError

# Extensions we accept. Audio is transcribed directly; video is extracted first.
AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "flac", "ogg", "opus", "aac", "wma"})
VIDEO_EXTENSIONS = frozenset({"mp4", "mov", "avi", "mkv", "webm", "mpeg", "mpg", "flv"})
ACCEPTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# Magic-byte signatures for content-based detection (offset, bytes, kind).
# Not exhaustive, but enough to catch the "renamed .txt → .mp3" case (M2.5).
_MAGIC: list[tuple[int, bytes, str]] = [
    (0, b"ID3", "audio"),  # MP3 with ID3
    (0, b"\xff\xfb", "audio"),  # MP3 frame sync
    (0, b"\xff\xf3", "audio"),
    (0, b"\xff\xf2", "audio"),
    (0, b"RIFF", "audio"),  # WAV (RIFF....WAVE)
    (0, b"OggS", "audio"),  # Ogg
    (0, b"fLaC", "audio"),  # FLAC
    (4, b"ftyp", "video"),  # MP4/MOV family (also m4a; treated as decodable)
    (0, b"\x1aE\xdf\xa3", "video"),  # Matroska/WebM (EBML)
    (0, b"FLV", "video"),  # FLV
    (0, b"\x00\x00\x01\xba", "video"),  # MPEG program stream
    (0, b"\x00\x00\x01\xb3", "video"),  # MPEG video sequence
    (0, b"RIFF", "video"),  # AVI is RIFF too; disambiguated below
]


def extension_of(filename: str) -> str:
    _, _, ext = filename.rpartition(".")
    return ext.lower() if "." in filename else ""


def is_video_extension(filename: str) -> bool:
    return extension_of(filename) in VIDEO_EXTENSIONS


def sniff_kind(head: bytes, filename: str) -> str:
    """Return ``'audio'`` | ``'video'`` | ``'unknown'`` from content + hint.

    Content wins; the extension is only a tie-breaker for RIFF (WAV vs AVI).
    """

    for offset, sig, kind in _MAGIC:
        if head[offset : offset + len(sig)] == sig:
            if sig == b"RIFF":
                # RIFF is both WAV and AVI; use the WAVE/AVI subtype at offset 8.
                subtype = head[8:12]
                if subtype == b"WAVE":
                    return "audio"
                if subtype == b"AVI ":
                    return "video"
                continue
            return kind
    # Content was inconclusive. Fall back to the extension hint, but only if the
    # content does not look like plain text — a `.txt` renamed to `.mp3` must be
    # rejected (M2.5: detect from content, not extension alone).
    if _looks_like_text(head):
        return "unknown"
    ext = extension_of(filename)
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "unknown"


def _looks_like_text(head: bytes) -> bool:
    """Heuristic: a head that is entirely printable ASCII/whitespace is text.

    Real audio/video containers carry non-text bytes in their first 32 bytes; a
    text file (the classic renamed-``.txt`` attack) does not.
    """

    if not head:
        return False
    printable = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}  # + tab/newline/CR
    return all(b in printable for b in head)


def validate_upload(filename: str, head: bytes) -> None:
    """Reject clearly-unsupported uploads up front (→ 4xx).

    A file whose content sniffs as neither audio nor video (e.g. a ``.txt``
    renamed to ``.mp3``) is rejected here rather than failing deep in the model.
    """

    kind = sniff_kind(head, filename)
    if kind == "unknown":
        raise UnsupportedMediaError(
            f"unsupported or unrecognised media: {filename!r} "
            f"(accepted: audio {sorted(AUDIO_EXTENSIONS)}, "
            f"video {sorted(VIDEO_EXTENSIONS)})"
        )


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class FfmpegStatus(str, Enum):
    """Reported by the health preflight (CR-016). Typed, not a magic string."""

    ok = "ok"
    missing = "missing"


def ffmpeg_status() -> FfmpegStatus:
    """Whether the FFmpeg CLI is present. Used by ``/health`` to surface a missing
    system dependency before a video job fails deep in extraction (CR-016)."""

    return FfmpegStatus.ok if ffmpeg_available() else FfmpegStatus.missing


def extract_audio(video_path: str) -> str:
    """Extract a mono 16 kHz WAV from a video with FFmpeg. Returns the WAV path.

    Raises :class:`UnsupportedMediaError` if FFmpeg is missing or the file cannot
    be read — the caller turns that into a ``failed`` job.
    """

    if not ffmpeg_available():
        raise UnsupportedMediaError(
            "ffmpeg is required to transcribe video but is not installed"
        )
    out_fd, out_path = tempfile.mkstemp(suffix=".wav")
    import os

    os.close(out_fd)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",  # drop video — audio only (D8)
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise UnsupportedMediaError(
            f"ffmpeg could not extract audio: {proc.stderr.strip()[:500]}"
        )
    return out_path
