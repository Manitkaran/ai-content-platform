"""M2.5 — content-based media detection + FFmpeg extraction path."""

from __future__ import annotations

import pytest

from app.media import (
    extract_audio,
    ffmpeg_available,
    is_video_extension,
    sniff_kind,
    validate_upload,
)
from app.model.contract import UnsupportedMediaError


def test_sniff_audio_from_content():
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt "
    assert sniff_kind(wav, "clip.mp3") == "audio"
    assert sniff_kind(b"ID3\x03abc", "x.mp3") == "audio"


def test_sniff_video_from_content():
    mp4 = b"\x00\x00\x00\x18ftypmp42"
    assert sniff_kind(mp4, "clip.mp4") == "video"
    mkv = b"\x1aE\xdf\xa3rest"
    assert sniff_kind(mkv, "clip.mkv") == "video"


def test_text_renamed_to_mp3_is_rejected():
    # Format detected from content, not extension (M2.5 acceptance).
    with pytest.raises(UnsupportedMediaError):
        validate_upload("evil.mp3", b"just some text, definitely not audio")


def test_video_extension_helper():
    assert is_video_extension("a.mp4")
    assert not is_video_extension("a.mp3")


def test_extract_audio_without_ffmpeg_is_typed_error(monkeypatch):
    monkeypatch.setattr("app.media.ffmpeg_available", lambda: False)
    with pytest.raises(UnsupportedMediaError):
        extract_audio("/tmp/whatever.mp4")


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
def test_extract_audio_on_corrupt_video_is_typed_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00\x00\x00\x18ftypmp42 but truncated garbage")
    with pytest.raises(UnsupportedMediaError):
        extract_audio(str(bad))
