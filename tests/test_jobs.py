"""M2.1 — job entity, typed status enum, legal transitions."""

from __future__ import annotations

import pytest

from app.jobs.models import IllegalTransition, Job, JobStatus
from app.model.contract import TranscribeOptions


def _job():
    return Job(
        id="j1",
        options=TranscribeOptions(),
        media_key="media/j1.mp3",
        original_filename="j1.mp3",
    )


def test_created_queued():
    job = _job()
    assert job.status == JobStatus.queued
    assert job.result is None


def test_legal_path():
    job = _job()
    job.transition_to(JobStatus.running)
    job.transition_to(JobStatus.completed)
    assert job.status == JobStatus.completed


def test_illegal_transition_rejected():
    job = _job()
    job.transition_to(JobStatus.running)
    job.transition_to(JobStatus.completed)
    with pytest.raises(IllegalTransition):
        job.transition_to(JobStatus.running)  # completed → running is illegal


def test_queued_cannot_jump_to_completed():
    job = _job()
    with pytest.raises(IllegalTransition):
        job.transition_to(JobStatus.completed)


def test_status_is_typed_enum():
    # A test forbidding raw status strings: the field type is the enum.
    job = _job()
    assert isinstance(job.status, JobStatus)
