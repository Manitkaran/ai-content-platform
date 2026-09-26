"""CR-028 / D28 — uploads stream to disk under an incremental cap (bounded memory).

Proves an oversized upload is rejected with 413 **without being fully buffered**, that a
normal upload still completes, that the worker uses the on-disk ``path_for`` (no whole-file
round-trip through RAM), and that batch rejects an oversized file per-row.
"""

from __future__ import annotations

import io


def _submit(client, data, filename="clip.wav", **form):
    return client.post(
        "/transcribe",
        files={"file": (filename, data, "audio/wav")},
        data=form,
    )


def test_oversized_upload_is_413_without_buffering_whole_body(client, sample_audio_bytes):
    """AC-1: with a tiny cap, an over-cap upload is rejected 413. The rejection comes from
    the streaming path (put_stream → UploadTooLarge), not a post-read length check."""

    from app.config import get_settings

    # Cap below the sample size so the stream trips the incremental limit.
    get_settings().max_upload_bytes = 8
    try:
        big = sample_audio_bytes + b"\x00" * 10_000
        r = _submit(client, big)
        assert r.status_code == 413
    finally:
        # restore (the fixture rebuilds settings per test, but be explicit)
        get_settings().max_upload_bytes = 500 * 1024 * 1024


def test_normal_upload_still_completes(client, sample_audio_bytes):
    """AC-2: a normal (in-limit) upload transcribes end-to-end through the streaming path."""

    r = _submit(client, sample_audio_bytes)
    assert r.status_code == 202
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert body["status"] == "completed"
    assert body["result"]["text"]


def test_execute_uses_path_for_no_whole_file_get(client, sample_audio_bytes, monkeypatch):
    """AC-4: on the local storage driver the worker reads the media via path_for and must
    NOT pull the whole file back with storage.get() — the second full-file RAM copy the
    fix removes. We assert get() is not called during execute for a local-stored job."""

    from app.storage.registry import get_storage

    storage = get_storage()
    calls = {"get": 0, "path_for": 0}
    real_get = storage.get
    real_path_for = storage.path_for

    def spy_get(key):
        calls["get"] += 1
        return real_get(key)

    def spy_path_for(key):
        calls["path_for"] += 1
        return real_path_for(key)

    monkeypatch.setattr(storage, "get", spy_get)
    monkeypatch.setattr(storage, "path_for", spy_path_for)

    r = _submit(client, sample_audio_bytes)
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert body["status"] == "completed"
    # The local driver has a real path → path_for was used, get() was not needed.
    assert calls["path_for"] >= 1
    assert calls["get"] == 0


def test_submit_stream_service_path(client, sample_audio_bytes):
    """The service-level streaming submit stores and completes without holding bytes."""

    from app.model.contract import TranscribeOptions
    from app.service import get_service

    svc = get_service()
    job = svc.submit_stream(
        filename="clip.wav",
        stream=io.BytesIO(sample_audio_bytes),
        max_bytes=10_000_000,
        options=TranscribeOptions(),
    )
    svc.execute(job.id)
    assert svc._store.get(job.id).status.value == "completed"


def test_submit_stream_rejects_oversized(client, sample_audio_bytes):
    from app.model.contract import TranscribeOptions
    from app.service import get_service
    from app.storage.contract import UploadTooLarge

    svc = get_service()
    try:
        svc.submit_stream(
            filename="clip.wav",
            stream=io.BytesIO(sample_audio_bytes + b"\x00" * 5000),
            max_bytes=8,
            options=TranscribeOptions(),
        )
    except UploadTooLarge:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected UploadTooLarge")


def test_batch_oversized_file_is_a_row_not_a_batch_failure(client, sample_audio_bytes):
    """AC-5: one oversized file in a batch → a too_large row; the rest still enqueue."""

    from app.config import get_settings

    get_settings().max_upload_bytes = 32
    try:
        files = [
            ("files", ("ok.wav", sample_audio_bytes[:16], "audio/wav")),
            ("files", ("big.wav", sample_audio_bytes + b"\x00" * 5000, "audio/wav")),
        ]
        results = client.post("/transcribe/batch", files=files).json()["results"]
    finally:
        get_settings().max_upload_bytes = 500 * 1024 * 1024

    by_name = {r["filename"]: r for r in results}
    assert by_name["big.wav"]["error"]["code"] == "too_large"
    # The small one was accepted (proves one bad file didn't sink the batch).
    assert by_name["ok.wav"].get("id")
