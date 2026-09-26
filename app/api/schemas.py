"""API request/response schemas (M2, M5.1).

These shape the generated OpenAPI (M5.1); each field carries a description and the
models carry examples so ``/docs`` is complete and the thin SDKs (M5.2/M5.3) have
one authoritative contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.jobs.models import Job, JobStatus
from app.model.contract import OutputFormat


class SegmentSchema(BaseModel):
    start: float = Field(description="Segment start time in seconds.")
    end: float = Field(description="Segment end time in seconds.")
    text: str = Field(description="Transcript text for this segment.")

    @model_validator(mode="after")
    def _check_times(self) -> SegmentSchema:
        # CR-018: reject nonsensical spans (used when editing subtitles).
        if self.start < 0:
            raise ValueError("segment start must be >= 0")
        if self.end < self.start:
            raise ValueError("segment end must be >= start")
        return self


class SegmentsEdit(BaseModel):
    """Body of ``PUT /jobs/{id}/segments`` — the corrected segments (CR-018).

    Each segment needs ``end >= start >= 0`` (enforced on ``SegmentSchema``); the
    list must be non-empty. Editing is segment-level only (D18), never re-runs the
    model, and never changes the job's state.
    """

    segments: list[SegmentSchema] = Field(
        min_length=1, description="The full replacement segment list (non-empty)."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "segments": [
                    {"start": 0.0, "end": 1.5, "text": "Corrected line one."},
                    {"start": 1.5, "end": 3.0, "text": "Corrected line two."},
                ]
            }
        }
    }


class ResultSchema(BaseModel):
    text: str = Field(description="The full transcript in the requested format.")
    detected_language: str = Field(
        description="The language detected or used (ISO code)."
    )
    segments: list[SegmentSchema] = Field(
        default_factory=list, description="Timestamped segments (segment-level)."
    )
    output_format: OutputFormat = Field(
        description="The format the transcript was rendered in."
    )


class ErrorSchema(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error detail.")


class BatchItemResult(BaseModel):
    """One file's outcome in a batch submission (CR-019).

    Either accepted (``id`` + ``status``) or rejected (``error``) — never both.
    Accepted files become ordinary jobs polled via ``GET /jobs/{id}``.
    """

    filename: str = Field(description="The submitted file's name.")
    id: str | None = Field(default=None, description="Job id if accepted.")
    status: JobStatus | None = Field(
        default=None, description="'queued' if accepted."
    )
    error: ErrorSchema | None = Field(
        default=None, description="Present only if this file was rejected."
    )


class BatchResponse(BaseModel):
    """Returned by ``POST /transcribe/batch`` — one result row per file (CR-019)."""

    results: list[BatchItemResult] = Field(
        description="Per-file outcomes, in submission order. Partial success is normal."
    )


class Template(BaseModel):
    """A named, reusable prompt preset (CR-022)."""

    name: str = Field(description="Unique name, [A-Za-z0-9_-]{1,64}.")
    prompt: str = Field(description="The Whisper initial_prompt this template applies.")
    description: str = Field(default="", description="Optional human note.")


class TemplateCreate(BaseModel):
    """Body of ``POST /templates`` (CR-022)."""

    name: str = Field(description="Unique name, [A-Za-z0-9_-]{1,64}.")
    prompt: str = Field(description="The prompt text to save.")
    description: str = Field(default="", description="Optional human note.")


class SubmitResponse(BaseModel):
    """Returned by ``POST /transcribe`` — the job id to poll."""

    id: str = Field(description="Job id; poll GET /jobs/{id}.")
    status: JobStatus = Field(description="Always 'queued' on submission.")

    model_config = {
        "json_schema_extra": {
            "example": {"id": "3f9a...", "status": "queued"}
        }
    }


class JobResponse(BaseModel):
    """Returned by ``GET /jobs/{id}`` — status, and result/error when terminal."""

    id: str
    status: JobStatus
    original_filename: str
    detected_language: str | None = Field(
        default=None, description="Present once completed."
    )
    result: ResultSchema | None = Field(
        default=None, description="Present only when status is 'completed'."
    )
    error: ErrorSchema | None = Field(
        default=None, description="Present only when status is 'failed'."
    )

    @classmethod
    def from_job(cls, job: Job, *, rendered_text: str | None = None) -> JobResponse:
        result_schema = None
        detected = None
        if job.status == JobStatus.completed and job.result is not None:
            detected = job.result.detected_language
            result_schema = ResultSchema(
                text=rendered_text
                if rendered_text is not None
                else job.result.text,
                detected_language=job.result.detected_language,
                segments=[
                    SegmentSchema(start=s.start, end=s.end, text=s.text)
                    for s in job.result.segments
                ],
                output_format=job.options.output_format,
            )
        error_schema = None
        if job.status == JobStatus.failed and job.error is not None:
            error_schema = ErrorSchema(code=job.error.code, message=job.error.message)
        return cls(
            id=job.id,
            status=job.status,
            original_filename=job.original_filename,
            detected_language=detected,
            result=result_schema,
            error=error_schema,
        )
