"""CR-018 — subtitle editing + format-override re-export."""

from __future__ import annotations


def _submit_and_complete(client, sample_audio_bytes):
    """Submit a job and return its completed body (execution is a background task,
    which the TestClient runs synchronously on the submitting request)."""

    r = client.post(
        "/transcribe", files={"file": ("clip.wav", sample_audio_bytes, "audio/wav")}
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    return job_id


EDIT = {
    "segments": [
        {"start": 0.0, "end": 1.5, "text": "Hello world."},
        {"start": 1.5, "end": 3.0, "text": "Second line."},
    ]
}


def test_edit_replaces_segments_and_rederives_text(client, sample_audio_bytes):
    job_id = _submit_and_complete(client, sample_audio_bytes)
    r = client.put(f"/jobs/{job_id}/segments", json=EDIT)
    assert r.status_code == 200
    result = r.json()["result"]
    assert [s["text"] for s in result["segments"]] == ["Hello world.", "Second line."]
    # text is re-derived from the edited segments (CR-018).
    assert result["text"] == "Hello world. Second line."


def test_edit_persists_across_get(client, sample_audio_bytes):
    job_id = _submit_and_complete(client, sample_audio_bytes)
    client.put(f"/jobs/{job_id}/segments", json=EDIT)
    again = client.get(f"/jobs/{job_id}").json()
    assert again["result"]["segments"][0]["text"] == "Hello world."


def test_download_format_override_uses_edited_segments(client, sample_audio_bytes):
    job_id = _submit_and_complete(client, sample_audio_bytes)
    client.put(f"/jobs/{job_id}/segments", json=EDIT)

    srt = client.get(f"/jobs/{job_id}/subtitle?format=srt")
    assert srt.status_code == 200
    assert "00:00:00,000 --> 00:00:01,500" in srt.text
    assert "Hello world." in srt.text

    vtt = client.get(f"/jobs/{job_id}/subtitle?format=vtt")
    assert vtt.status_code == 200
    assert vtt.text.startswith("WEBVTT")
    assert "00:00:01.500 --> 00:00:03.000" in vtt.text

    txt = client.get(f"/jobs/{job_id}/subtitle?format=txt")
    assert txt.text == "Hello world. Second line."


def test_edit_on_unknown_job_is_404(client):
    r = client.put("/jobs/nope/segments", json=EDIT)
    assert r.status_code == 404


def test_edit_empty_segments_is_422(client, sample_audio_bytes):
    job_id = _submit_and_complete(client, sample_audio_bytes)
    r = client.put(f"/jobs/{job_id}/segments", json={"segments": []})
    assert r.status_code == 422


def test_edit_end_before_start_is_422(client, sample_audio_bytes):
    job_id = _submit_and_complete(client, sample_audio_bytes)
    r = client.put(
        f"/jobs/{job_id}/segments",
        json={"segments": [{"start": 2.0, "end": 1.0, "text": "bad"}]},
    )
    assert r.status_code == 422


def test_edit_non_completed_job_raises(client, sample_audio_bytes):
    """A queued job has no transcript to edit → JobNotCompleted (→ 409).

    Driven at the service layer because the TestClient completes jobs
    synchronously, so a ``queued`` job can't be observed over HTTP.
    """

    import pytest

    from app.model.contract import Segment, TranscribeOptions
    from app.service import JobNotCompleted, get_service

    service = get_service()
    job = service.submit(
        filename="clip.wav", data=sample_audio_bytes, options=TranscribeOptions()
    )
    # Job is 'queued' — never executed, so there is no transcript.
    with pytest.raises(JobNotCompleted):
        service.edit_segments(job.id, [Segment(start=0.0, end=1.0, text="x")])
