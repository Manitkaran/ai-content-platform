"""CR-026 / D26 — atomic job claim (no double-execution) + SQLite WAL.

Proves ``claim`` is an atomic compare-and-swap ``queued → running`` on both stores
(in-memory and SQLite, at parity), that N concurrent claimers win exactly once, that
``execute`` run twice (API + worker racing) transcribes once, and that SQLite opens in
WAL with a busy timeout.
"""

from __future__ import annotations

import sqlite3
import threading

from app.config import get_settings
from app.jobs.models import Job, JobStatus
from app.jobs.store import JobStore, SqliteJobStore, get_job_store
from app.model.contract import OutputFormat, TranscribeOptions


def _job(job_id: str = "j1") -> Job:
    return Job(
        id=job_id,
        options=TranscribeOptions(output_format=OutputFormat.text),
        media_key=f"media/{job_id}",
        original_filename="clip.wav",
    )


# --- claim semantics, both stores at parity ----------------------------------


def _stores(tmp_path):
    return [
        ("memory", JobStore()),
        ("sqlite", SqliteJobStore(str(tmp_path / "acp.db"))),
    ]


def test_claim_moves_queued_to_running(tmp_path):
    for name, store in _stores(tmp_path):
        store.add(_job())
        claimed = store.claim("j1")
        assert claimed is not None, name
        assert claimed.status is JobStatus.running, name
        # The change is persisted, not just returned.
        assert store.get("j1").status is JobStatus.running, name


def test_claim_returns_none_when_not_queued(tmp_path):
    for name, store in _stores(tmp_path):
        store.add(_job())
        assert store.claim("j1") is not None, name  # first wins
        assert store.claim("j1") is None, name       # already running → no-op
        # A terminal job also can't be claimed.
        job = store.get("j1")
        job.transition_to(JobStatus.completed)
        store.save(job)
        assert store.claim("j1") is None, name


def test_claim_missing_job_returns_none(tmp_path):
    for name, store in _stores(tmp_path):
        assert store.claim("nope") is None, name


def test_claim_is_atomic_under_concurrency(tmp_path):
    """AC-2: N threads claim the same queued job → exactly one wins (the CAS)."""

    for name, store in _stores(tmp_path):
        store.add(_job())
        wins: list[Job] = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker(store=store, barrier=barrier, lock=lock, wins=wins):
            barrier.wait()  # maximise the race
            got = store.claim("j1")
            if got is not None:
                with lock:
                    wins.append(got)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wins) == 1, f"{name}: expected exactly one winner, got {len(wins)}"


# --- SQLite pragmas ----------------------------------------------------------


def test_sqlite_opens_in_wal_with_busy_timeout(tmp_path):
    """AC-5: WAL journal + a busy timeout are set at construction."""

    db = str(tmp_path / "acp.db")
    SqliteJobStore(db)  # constructing sets the pragmas + creates the file
    probe = sqlite3.connect(db)
    try:
        mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = probe.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        probe.close()
    assert mode.lower() == "wal"
    assert timeout >= 1  # a positive wait, not the default 0 (fail-fast)


# --- service-level: double execute runs once ---------------------------------


def test_double_execute_runs_once(client, sample_audio_bytes):
    """AC-3: two execute() calls (API + worker racing) → the job runs exactly once."""

    from app.service import get_service

    svc = get_service()
    job = svc.submit(
        filename="clip.wav", data=sample_audio_bytes, options=TranscribeOptions()
    )
    svc.execute(job.id)
    done = svc._store.get(job.id)
    assert done.status is JobStatus.completed

    # The second call must be a silent no-op — not re-run, not flip to failed even
    # though the media was already deleted on the first completion (D16).
    svc.execute(job.id)
    still = svc._store.get(job.id)
    assert still.status is JobStatus.completed
    assert still.result is not None
    assert still.result == done.result


def test_execute_on_missing_job_is_noop(client):
    """AC-4: a job deleted between submit and execute → silent no-op, no exception."""

    from app.service import get_service

    svc = get_service()
    svc.execute("does-not-exist")  # must not raise


def test_get_job_store_is_sqlite_in_tests():
    """Guards that the conftest DB URL selects the real SQLite store (parity proven)."""

    assert isinstance(get_job_store(), SqliteJobStore)
    assert get_settings().database_url.startswith("sqlite:///")
