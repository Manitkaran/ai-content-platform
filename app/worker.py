"""Background worker entrypoint (M0.4, D11).

Phase 1 executes transcription as FastAPI background tasks in the API process, so
the ``worker`` service's job is the *out-of-band* work that must happen off the
request even when no request is in flight:

  * pick up any job still ``queued`` (e.g. if the API restarted mid-flight), and
  * sweep expired transcripts on the retention schedule (D16 / M4.2).

Run with: ``python -m app.worker``. Same image as the API, different command —
"one image, only the command differs" (acp-architecture).

Since CR-017 the store is a **shared SQLite DB** (D11), so the worker and the API
are separate processes reading the same jobs. Both may try to execute a queued
job — the API via a background task, the worker via this drain loop — so execution
is claimed **atomically** in the store (``claim``, CR-026): whichever process wins
the ``queued → running`` compare-and-swap runs the job, the other no-ops. The
worker therefore safely (a) drains queued jobs a restart left behind or the API
never scheduled, and (b) sweeps expired transcripts on the retention schedule
(D16 / M4.2), with no double-execution.
"""

from __future__ import annotations

import time

from app.config import get_settings
from app.jobs.models import JobStatus
from app.service import get_service


def run_once() -> dict[str, int]:
    """One worker tick: drain queued jobs, then sweep expired transcripts."""

    service = get_service()
    store = service._store  # worker owns the store lifecycle
    drained = 0
    for job in store.all():
        if job.status == JobStatus.queued:
            service.execute(job.id)
            drained += 1
    swept = service.sweep_expired()
    return {"drained": drained, "swept": swept}


def main(poll_seconds: float = 5.0) -> None:  # pragma: no cover - loop
    settings = get_settings()
    print(
        f"[worker] started env={settings.environment.value} "
        f"model={settings.model_driver.value} tier={settings.model_tier.value}"
    )
    while True:
        stats = run_once()
        if stats["drained"] or stats["swept"]:
            print(f"[worker] {stats}")
        time.sleep(poll_seconds)


if __name__ == "__main__":  # pragma: no cover
    main()
