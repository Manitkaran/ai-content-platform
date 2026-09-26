"""CR-022 — prompt (Whisper initial_prompt) threading + length cap."""

from __future__ import annotations


def _submit(client, sample_audio_bytes, **data):
    r = client.post(
        "/transcribe",
        files={"file": ("clip.wav", sample_audio_bytes, "audio/wav")},
        data=data,
    )
    return r


def test_prompt_threads_through_to_the_seam(client, sample_audio_bytes):
    """The fake driver echoes the prompt, proving it reached the model call."""

    r = _submit(client, sample_audio_bytes, prompt="Acme Corp, Kubernetes")
    assert r.status_code == 202
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert body["status"] == "completed"
    assert "[prompt:Acme Corp, Kubernetes]" in body["result"]["text"]


def test_no_prompt_is_unchanged(client, sample_audio_bytes):
    r = _submit(client, sample_audio_bytes)
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert "[prompt:" not in body["result"]["text"]


def test_prompt_over_limit_is_422(client, sample_audio_bytes):
    huge = "x" * 5000  # default cap is 2000
    r = _submit(client, sample_audio_bytes, prompt=huge)
    assert r.status_code == 422


def test_prompt_persists_on_the_job(client, sample_audio_bytes):
    """The chosen prompt round-trips through the (SQLite) store as part of options."""

    r = _submit(client, sample_audio_bytes, prompt="glossary terms")
    from app.jobs.store import get_job_store

    job = get_job_store().get(r.json()["id"])
    assert job.options.prompt == "glossary terms"
