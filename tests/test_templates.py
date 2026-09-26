"""CR-022 — prompt-template CRUD + resolve-by-name on submit."""

from __future__ import annotations


def test_template_crud(client):
    # empty to start
    assert client.get("/templates").json() == []

    # create
    r = client.post(
        "/templates",
        json={"name": "medical", "prompt": "ECG, tachycardia, mmHg", "description": "med"},
    )
    assert r.status_code == 200
    assert r.json()["prompt"] == "ECG, tachycardia, mmHg"

    # list shows it
    names = [t["name"] for t in client.get("/templates").json()]
    assert names == ["medical"]

    # update (upsert)
    client.post("/templates", json={"name": "medical", "prompt": "updated terms"})
    assert client.get("/templates").json()[0]["prompt"] == "updated terms"

    # delete
    assert client.delete("/templates/medical").status_code == 204
    assert client.get("/templates").json() == []


def test_invalid_template_name_is_422(client):
    r = client.post("/templates", json={"name": "bad name!", "prompt": "x"})
    assert r.status_code == 422


def test_submit_with_template_resolves_prompt(client, sample_audio_bytes):
    client.post("/templates", json={"name": "legal", "prompt": "voir dire, tort"})
    r = client.post(
        "/transcribe",
        files={"file": ("clip.wav", sample_audio_bytes, "audio/wav")},
        data={"template": "legal"},
    )
    assert r.status_code == 202
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert "[prompt:voir dire, tort]" in body["result"]["text"]


def test_explicit_prompt_overrides_template(client, sample_audio_bytes):
    client.post("/templates", json={"name": "legal", "prompt": "voir dire"})
    r = client.post(
        "/transcribe",
        files={"file": ("clip.wav", sample_audio_bytes, "audio/wav")},
        data={"template": "legal", "prompt": "explicit wins"},
    )
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert "[prompt:explicit wins]" in body["result"]["text"]


def test_submit_with_unknown_template_is_422(client, sample_audio_bytes):
    r = client.post(
        "/transcribe",
        files={"file": ("clip.wav", sample_audio_bytes, "audio/wav")},
        data={"template": "does-not-exist"},
    )
    assert r.status_code == 422
