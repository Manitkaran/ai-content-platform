"""CR-020 — JSON output format."""

from __future__ import annotations

import json

from app.model.contract import OutputFormat, Segment, TranscriptResult
from app.output.subtitles import content_type_for, file_extension_for, render


def _result() -> TranscriptResult:
    return TranscriptResult(
        text="नमस्ते world",
        detected_language="hi",
        segments=[
            Segment(start=0.0, end=1.5, text="नमस्ते"),
            Segment(start=1.5, end=3.0, text="world"),
        ],
    )


def test_render_json_roundtrips_to_the_dto():
    out = render(_result(), OutputFormat.json)
    parsed = json.loads(out)
    assert parsed["text"] == "नमस्ते world"
    assert parsed["detected_language"] == "hi"
    assert parsed["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "नमस्ते"},
        {"start": 1.5, "end": 3.0, "text": "world"},
    ]


def test_json_content_type_and_extension():
    assert content_type_for(OutputFormat.json) == "application/json; charset=utf-8"
    assert file_extension_for(OutputFormat.json) == "json"


def _submit_and_complete(client, sample_audio_bytes, output_format="text"):
    r = client.post(
        "/transcribe",
        files={"file": ("clip.wav", sample_audio_bytes, "audio/wav")},
        data={"output_format": output_format},
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "completed"
    return job_id


def test_download_format_json_override(client, sample_audio_bytes):
    job_id = _submit_and_complete(client, sample_audio_bytes)
    r = client.get(f"/jobs/{job_id}/subtitle?format=json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.text)
    assert "text" in body and "segments" in body and "detected_language" in body


def test_submit_with_output_format_json(client, sample_audio_bytes):
    """A job submitted as output_format=json renders JSON in GET /jobs too."""

    job_id = _submit_and_complete(client, sample_audio_bytes, output_format="json")
    # The subtitle download (no ?format) uses the job's own format → json.
    r = client.get(f"/jobs/{job_id}/subtitle")
    assert r.headers["content-type"].startswith("application/json")
    json.loads(r.text)  # parses without error


def test_json_reflects_edited_segments(client, sample_audio_bytes):
    """CR-020 + CR-018: editing then exporting JSON shows the edit."""

    job_id = _submit_and_complete(client, sample_audio_bytes)
    client.put(
        f"/jobs/{job_id}/segments",
        json={"segments": [{"start": 0.0, "end": 2.0, "text": "Edited."}]},
    )
    body = json.loads(client.get(f"/jobs/{job_id}/subtitle?format=json").text)
    assert body["text"] == "Edited."
    assert body["segments"] == [{"start": 0.0, "end": 2.0, "text": "Edited."}]
