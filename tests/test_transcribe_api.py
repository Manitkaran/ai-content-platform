"""M2.2 / M2.3 / M3.1 / M3.2 — the submit→poll API on the fake driver."""

from __future__ import annotations


def _submit(client, sample_audio_bytes, filename="clip.mp3", **form):
    return client.post(
        "/transcribe",
        files={"file": (filename, sample_audio_bytes, "audio/mpeg")},
        data=form,
    )


def test_submit_returns_queued_id(client, sample_audio_bytes):
    r = _submit(client, sample_audio_bytes)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["id"]


def test_job_reaches_completed_with_fake_result(client, sample_audio_bytes):
    # TestClient runs background tasks synchronously after the response, so by
    # the time we poll the job has executed.
    sub = _submit(client, sample_audio_bytes).json()
    r = client.get(f"/jobs/{sub['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["result"]["text"]  # the fake transcript
    assert body["detected_language"]


def test_unknown_job_is_404(client):
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404


def test_unsupported_upload_is_4xx(client):
    # a .txt renamed to .mp3 — content sniffs as neither audio nor video
    r = client.post(
        "/transcribe",
        files={"file": ("evil.mp3", b"this is plain text not audio", "audio/mpeg")},
        data={},
    )
    assert r.status_code == 415
    # app stays up
    assert client.get("/health").status_code == 200


def test_oversized_upload_is_413(client, sample_audio_bytes, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 4, raising=False)
    r = _submit(client, sample_audio_bytes)
    assert r.status_code == 413


def test_language_override_echoed(client, sample_audio_bytes):
    sub = _submit(client, sample_audio_bytes, language="es").json()
    body = client.get(f"/jobs/{sub['id']}").json()
    assert body["detected_language"] == "es"


def test_unknown_language_is_422(client, sample_audio_bytes):
    r = _submit(client, sample_audio_bytes, language="zz")
    assert r.status_code == 422


def test_translate_flag_wires_through(client, sample_audio_bytes):
    sub = _submit(client, sample_audio_bytes, translate="true").json()
    body = client.get(f"/jobs/{sub['id']}").json()
    # the fake echoes "translated" into the text when translate=true
    assert "translated" in body["result"]["text"]
