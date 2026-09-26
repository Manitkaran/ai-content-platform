"""M4.2 / M4.3 — retention, stale-link 410, failure & empty-result paths."""

from __future__ import annotations

from datetime import timedelta

from app.config import get_settings
from app.jobs.models import JobStatus, _now
from app.jobs.store import get_job_store
from app.model.contract import TranscribeOptions, TranscriptionError
from app.service import TranscriptionService
from app.storage.registry import get_storage


def _service():
    from app.model.drivers.fake import FakeTranscriber
    from app.translate.drivers.fake import FakeTranslator

    return TranscriptionService(
        store=get_job_store(),
        storage=get_storage(),
        transcriber=FakeTranscriber(),
        settings=get_settings(),
        translator=FakeTranslator(),  # CR-025
    )


def test_media_deleted_on_completion(tmp_path):
    svc = _service()
    job = svc.submit(
        filename="clip.mp3", data=b"RIFF\x00\x00\x00\x00WAVE", options=TranscribeOptions()
    )
    assert svc._storage.exists(job.media_key)
    svc.execute(job.id)
    done = svc._store.get(job.id)
    assert done.status == JobStatus.completed
    assert done.media_deleted is True
    assert not svc._storage.exists(job.media_key)  # source removed (D16)
    assert done.expires_at is not None  # transcript kept N days


def test_empty_clip_completes_empty_not_failed():
    svc = _service()
    job = svc.submit(
        filename="silence.wav", data=b"RIFF\x00\x00\x00\x00WAVE", options=TranscribeOptions()
    )
    svc.execute(job.id)
    done = svc._store.get(job.id)
    assert done.status == JobStatus.completed  # not failed (D12)
    assert done.result.text == ""


def test_driver_failure_becomes_failed_state():
    svc = _service()

    class Boom:
        def transcribe(self, media_path, options):
            raise TranscriptionError("boom")

    svc._transcriber = Boom()
    job = svc.submit(
        filename="clip.mp3", data=b"RIFF\x00\x00\x00\x00WAVE", options=TranscribeOptions()
    )
    svc.execute(job.id)
    done = svc._store.get(job.id)
    assert done.status == JobStatus.failed
    assert done.error is not None


def test_sweep_deletes_expired_transcript():
    svc = _service()
    job = svc.submit(
        filename="clip.mp3", data=b"RIFF\x00\x00\x00\x00WAVE", options=TranscribeOptions()
    )
    svc.execute(job.id)
    done = svc._store.get(job.id)
    done.expires_at = _now() - timedelta(days=1)  # already expired
    svc._store.save(done)
    removed = svc.sweep_expired()
    assert removed == 1
    assert not svc._store.has(job.id)


def test_stale_link_after_expiry_is_not_500(client, sample_audio_bytes):
    # Submit + complete, then expire and sweep, then poll → 404 (job gone), not 500.
    sub = client.post(
        "/transcribe",
        files={"file": ("clip.mp3", sample_audio_bytes, "audio/mpeg")},
        data={},
    ).json()
    store = get_job_store()
    job = store.get(sub["id"])
    job.expires_at = _now() - timedelta(days=1)
    store.save(job)
    from app.service import get_service

    get_service().sweep_expired()
    r = client.get(f"/jobs/{sub['id']}")
    assert r.status_code == 404  # clean gone-status, never a 500
