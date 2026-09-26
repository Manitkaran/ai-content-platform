"""The ``Job`` entity + typed status enum + legal transitions (M2.1, D12).

Lifecycle (D12, resolves O6): ``queued → running → completed | failed``. No
cancel, no retry in Phase 1. An empty-speech clip is ``completed`` with empty
text, not ``failed`` (M4.3).

Jobs have **no owner column** — single operator, no auth (D10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from app.model.contract import TranscribeOptions, TranscriptResult


class JobStatus(str, Enum):
    """The only legal job states (D12). A typed enum, never a raw string."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


# Legal state transitions. Anything not listed is rejected (M2.1 acceptance:
# e.g. completed → running is illegal). Terminal states have no outgoing edges.
LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.queued: frozenset({JobStatus.running, JobStatus.failed}),
    JobStatus.running: frozenset({JobStatus.completed, JobStatus.failed}),
    JobStatus.completed: frozenset(),
    JobStatus.failed: frozenset(),
}


class IllegalTransition(Exception):
    """Raised when code attempts a transition not in ``LEGAL_TRANSITIONS``."""


@dataclass(frozen=True)
class JobError:
    """A typed failure recorded on a ``failed`` job (never a bare 500)."""

    code: str
    message: str


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Job:
    """A unit of transcription work. Constructed in ``queued``."""

    id: str
    options: TranscribeOptions
    media_key: str  # object-storage key of the uploaded media
    original_filename: str
    status: JobStatus = JobStatus.queued
    result: TranscriptResult | None = None
    error: JobError | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    # Set when media is deleted on completion (D16); transcript is kept N days.
    media_deleted: bool = False
    # When the transcript is swept (D16). None until completion.
    expires_at: datetime | None = None

    def transition_to(self, new_status: JobStatus) -> None:
        """Move to ``new_status`` if the edge is legal, else raise."""

        if new_status not in LEGAL_TRANSITIONS[self.status]:
            raise IllegalTransition(
                f"illegal transition {self.status.value} → {new_status.value}"
            )
        self.status = new_status
        self.updated_at = _now()
