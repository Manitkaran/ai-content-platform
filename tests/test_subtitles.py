"""M3.3 — SRT/VTT formatters + the format option end to end."""

from __future__ import annotations

from app.model.contract import OutputFormat, Segment, TranscriptResult
from app.output.subtitles import render, to_srt, to_vtt


def _result():
    return TranscriptResult(
        text="hello world",
        detected_language="en",
        segments=[
            Segment(start=0.0, end=1.5, text="hello"),
            Segment(start=1.5, end=3.25, text="world"),
        ],
    )


def test_srt_timecodes():
    srt = to_srt(_result().segments)
    assert "1\n00:00:00,000 --> 00:00:01,500\nhello" in srt
    assert "2\n00:00:01,500 --> 00:00:03,250\nworld" in srt


def test_vtt_header_and_timecodes():
    vtt = to_vtt(_result().segments)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in vtt


def test_render_text_is_plain():
    assert render(_result(), OutputFormat.text) == "hello world"


def test_api_download_srt(client, sample_audio_bytes):
    sub = client.post(
        "/transcribe",
        files={"file": ("clip.mp3", sample_audio_bytes, "audio/mpeg")},
        data={"output_format": "srt"},
    ).json()
    r = client.get(f"/jobs/{sub['id']}/subtitle")
    assert r.status_code == 200
    assert "application/x-subrip" in r.headers["content-type"]
    assert "-->" in r.text  # a valid-looking SRT body
    assert "attachment" in r.headers["content-disposition"]
