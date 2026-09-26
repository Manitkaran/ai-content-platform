"""The job store (M2.1).

An in-process, thread-safe store keyed by job id. It is sufficient for Phase 1's
single-operator deployment (D10) and for the async background worker (D11), which
runs in the same process (FastAPI background tasks). Swapping in SQLite/Postgres
later is a driver change behind this same interface.

Test-only guard: in the ``testing`` environment the store is isolated per
construction (a fresh dict), mirroring the platform's ``*_testing`` rule that a
test run never shares a datastore with development.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache

from app.config import Environment, get_settings
from app.jobs.models import Job, JobError, JobStatus
from app.model.contract import (
    OutputFormat,
    Segment,
    TranscribeOptions,
    TranscriptResult,
)


class JobNotFound(KeyError):
    """No job with the given id (→ 404 at the API layer)."""


class JobStore:
    """Thread-safe in-memory ``Job`` store."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobNotFound(job_id) from exc

    def has(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def save(self, job: Job) -> None:
        """Persist a mutated job (no-op for in-memory, kept for driver parity)."""

        with self._lock:
            self._jobs[job.id] = job

    def claim(self, job_id: str) -> Job | None:
        """Atomically move a ``queued`` job to ``running`` and return it (CR-026).

        Returns the now-``running`` job if the caller won the claim, or ``None`` if the
        job was already running/terminal or does not exist. The whole check-and-flip runs
        under the store lock, so two racing callers (the API background task and the
        worker, D11) can never both win — the job runs exactly once (D12; media is deleted
        on first completion, D16, so a second run would only corrupt the record). The
        SQLite driver overrides this with a single-statement compare-and-swap for the
        cross-process case."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.queued:
                return None
            job.transition_to(JobStatus.running)
            self._jobs[job_id] = job
            return job

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)


# --- SQLite driver (CR-017) --------------------------------------------------
#
# Same six-method contract as JobStore, but durable: jobs survive an API restart
# and are visible to the worker process over a shared DB file. Job (and its DTOs)
# is (de)serialized to a single JSON `payload` column; `status`/`expires_at` are
# broken out as indexed columns for the worker's queued-drain + retention sweep.


def _serialize(job: Job) -> str:
    """Job → JSON string. Enums to their values, datetimes to ISO-8601."""

    d = asdict(job)
    d["status"] = job.status.value
    d["options"] = {**d["options"], "output_format": job.options.output_format.value}
    d["created_at"] = job.created_at.isoformat()
    d["updated_at"] = job.updated_at.isoformat()
    d["expires_at"] = job.expires_at.isoformat() if job.expires_at else None
    return json.dumps(d)


def _deserialize(payload: str) -> Job:
    """JSON string → Job, reversing :func:`_serialize` exactly (round-trip safe)."""

    d = json.loads(payload)
    opts = d["options"]
    options = TranscribeOptions(
        language=opts["language"],
        translate=opts["translate"],
        output_format=OutputFormat(opts["output_format"]),
        prompt=opts.get("prompt", ""),  # CR-022; old rows have no prompt
        target_language=opts.get("target_language", ""),  # CR-025; old rows have none
    )
    result = None
    if d.get("result") is not None:
        r = d["result"]
        result = TranscriptResult(
            text=r["text"],
            detected_language=r["detected_language"],
            segments=[Segment(**s) for s in r.get("segments", [])],
        )
    error = JobError(**d["error"]) if d.get("error") is not None else None
    return Job(
        id=d["id"],
        options=options,
        media_key=d["media_key"],
        original_filename=d["original_filename"],
        status=JobStatus(d["status"]),
        result=result,
        error=error,
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        media_deleted=d["media_deleted"],
        expires_at=datetime.fromisoformat(d["expires_at"]) if d["expires_at"] else None,
    )


class SqliteJobStore(JobStore):
    """Durable job store (CR-017). Same interface as :class:`JobStore`.

    Subclasses ``JobStore`` so the service's ``store: JobStore`` type still holds
    and the in-memory base remains the parity reference. One table, one row per
    job; a single lock serialises writes (single-operator load, D10).
    """

    def __init__(self, db_path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        # CR-026: the API and the worker are separate processes sharing this DB (D11,
        # docker compose). WAL lets readers not block the writer and behaves far better
        # multi-process than the default rollback journal; busy_timeout waits for a lock
        # (up to 5 s) instead of erroring immediately — otherwise cross-process writes
        # surface as spurious ``database is locked`` → internal_error failed jobs.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "expires_at TEXT, payload TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status)"
        )
        self._conn.commit()

    def _write(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, status, expires_at, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, expires_at=excluded.expires_at, "
                "payload=excluded.payload",
                (
                    job.id,
                    job.status.value,
                    job.expires_at.isoformat() if job.expires_at else None,
                    _serialize(job),
                    job.created_at.isoformat(),
                ),
            )
            self._conn.commit()

    def add(self, job: Job) -> None:
        self._write(job)

    def save(self, job: Job) -> None:
        self._write(job)

    def claim(self, job_id: str) -> Job | None:
        """Atomically move a ``queued`` job to ``running`` and return it (CR-026).

        The ``WHERE status='queued'`` clause is the compare-and-swap: a single UPDATE
        statement, so there is no read-then-write window. ``cursor.rowcount == 1`` means
        this caller won the claim; ``0`` means another actor (the other process' worker /
        background task) already claimed it, or the job is gone. Both the ``status`` column
        and the ``status`` inside the JSON ``payload`` are updated together so a subsequent
        ``get`` (which deserialises the payload) reflects ``running``. Returns the
        now-running job on a win, else ``None`` — the cross-process twin of the base
        store's locked claim, at parity."""

        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = _deserialize(row[0])
            if job.status != JobStatus.queued:
                return None
            job.transition_to(JobStatus.running)
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, payload = ? "
                "WHERE id = ? AND status = ?",
                (job.status.value, _serialize(job), job_id, JobStatus.queued.value),
            )
            self._conn.commit()
            if cur.rowcount != 1:
                return None  # lost the race — another actor claimed it first
            return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return _deserialize(row[0])

    def has(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return row is not None

    def all(self) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM jobs ORDER BY created_at"
            ).fetchall()
        return [_deserialize(r[0]) for r in rows]

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()


def _db_path_from_url(url: str) -> str | None:
    """Extract a filesystem path from a ``sqlite:///path`` URL.

    Returns ``None`` for ``:memory:`` (or unparseable) so callers fall back to the
    in-memory store — keeping an escape hatch and never touching a non-sqlite URL.
    """

    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    path = url[len(prefix) :]
    if not path or path == ":memory:":
        return None
    return path


@lru_cache
def get_job_store() -> JobStore:
    """Process-wide job store singleton.

    SQLite-backed by default (CR-017) so jobs survive a restart; falls back to the
    in-memory store for a ``:memory:``/unparseable URL. The conftest points
    ``ACP_DATABASE_URL`` at a per-test temp file, so tests exercise the real
    SQLite store in isolation.
    """

    settings = get_settings()
    db_path = _db_path_from_url(settings.database_url)
    if db_path is None:
        return JobStore()
    return SqliteJobStore(db_path)


def reset_job_store_cache() -> None:
    get_job_store.cache_clear()


def is_test_isolated() -> bool:
    return get_settings().environment == Environment.testing
