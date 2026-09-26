"""CR-017 — the SQLite job store persists jobs across store instances (restart)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.jobs.models import Job, JobError, JobStatus
from app.jobs.store import JobNotFound, SqliteJobStore
from app.model.contract import (
    OutputFormat,
    Segment,
    TranscribeOptions,
    TranscriptResult,
)


def _job(job_id: str = "j1") -> Job:
    return Job(
        id=job_id,
        options=TranscribeOptions(
            language="hi", translate=True, output_format=OutputFormat.srt
        ),
        media_key=f"media/{job_id}",
        original_filename="clip.mp4",
    )


def test_add_and_get_roundtrip(tmp_path):
    store = SqliteJobStore(str(tmp_path / "acp.db"))
    store.add(_job())
    got = store.get("j1")
    assert got.id == "j1"
    assert got.options.language == "hi"
    assert got.options.translate is True
    assert got.options.output_format is OutputFormat.srt
    assert got.status is JobStatus.queued


def test_survives_new_store_over_same_file(tmp_path):
    """The restart proof: a fresh store over the same DB file still sees the job."""

    db = str(tmp_path / "acp.db")
    first = SqliteJobStore(db)
    job = _job()
    job.transition_to(JobStatus.running)
    job.transition_to(JobStatus.completed)
    job.result = TranscriptResult(
        text="नमस्ते",
        detected_language="hi",
        segments=[Segment(start=0.0, end=1.5, text="नमस्ते")],
    )
    job.expires_at = datetime(2030, 1, 1, tzinfo=UTC)
    first.save(job)

    # Simulate a restart: brand-new store instance, same file.
    second = SqliteJobStore(db)
    got = second.get("j1")
    assert got.status is JobStatus.completed
    assert got.result is not None
    assert got.result.text == "नमस्ते"
    assert got.result.segments[0].end == 1.5
    assert got.expires_at == datetime(2030, 1, 1, tzinfo=UTC)


def test_save_updates_in_place(tmp_path):
    store = SqliteJobStore(str(tmp_path / "acp.db"))
    store.add(_job())
    job = store.get("j1")
    job.transition_to(JobStatus.running)
    job.transition_to(JobStatus.failed)
    job.error = JobError(code="unsupported_media", message="bad codec")
    store.save(job)
    got = store.get("j1")
    assert got.status is JobStatus.failed
    assert got.error is not None
    assert got.error.code == "unsupported_media"


def test_has_delete_and_all(tmp_path):
    store = SqliteJobStore(str(tmp_path / "acp.db"))
    store.add(_job("a"))
    store.add(_job("b"))
    assert store.has("a") and store.has("b")
    assert {j.id for j in store.all()} == {"a", "b"}
    store.delete("a")
    assert not store.has("a")
    assert {j.id for j in store.all()} == {"b"}


def test_get_missing_raises(tmp_path):
    store = SqliteJobStore(str(tmp_path / "acp.db"))
    try:
        store.get("nope")
    except JobNotFound:
        pass
    else:
        raise AssertionError("expected JobNotFound")
