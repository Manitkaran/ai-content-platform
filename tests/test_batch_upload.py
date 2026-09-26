"""CR-019 — batch / multi-file upload."""

from __future__ import annotations


def test_batch_mixed_accept_and_reject(client, sample_audio_bytes):
    """Good + unsupported + empty in one batch → per-file rows, no whole-request 4xx."""

    files = [
        ("files", ("a.wav", sample_audio_bytes, "audio/wav")),
        ("files", ("note.txt", b"just plain text, not media", "text/plain")),
        ("files", ("empty.wav", b"", "audio/wav")),
        ("files", ("b.wav", sample_audio_bytes, "audio/wav")),
    ]
    r = client.post("/transcribe/batch", files=files)
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["filename"] for x in results] == ["a.wav", "note.txt", "empty.wav", "b.wav"]

    assert results[0]["id"] and results[0]["status"] == "queued"
    assert results[1]["error"]["code"] == "unsupported_media"
    assert results[2]["error"]["code"] == "empty_upload"
    assert results[3]["id"] and results[3]["status"] == "queued"


def test_batch_accepted_jobs_complete(client, sample_audio_bytes):
    files = [
        ("files", ("a.wav", sample_audio_bytes, "audio/wav")),
        ("files", ("b.wav", sample_audio_bytes, "audio/wav")),
    ]
    results = client.post("/transcribe/batch", files=files).json()["results"]
    for row in results:
        got = client.get(f"/jobs/{row['id']}")
        assert got.status_code == 200
        assert got.json()["status"] == "completed"


def test_batch_over_cap_is_413(client, sample_audio_bytes, monkeypatch):
    # Shrink the cap so we don't have to build 20+ files.
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_batch_files", 2, raising=True)
    files = [
        ("files", (f"f{i}.wav", sample_audio_bytes, "audio/wav")) for i in range(3)
    ]
    r = client.post("/transcribe/batch", files=files)
    assert r.status_code == 413


def test_batch_unknown_language_is_422(client, sample_audio_bytes):
    files = [("files", ("a.wav", sample_audio_bytes, "audio/wav"))]
    r = client.post("/transcribe/batch", files=files, data={"language": "zz-not-real"})
    assert r.status_code == 422
