"""API routes: /health, POST /transcribe, GET /jobs/{id}, subtitle download.

Conventions: failure is a state (not a 500); unknown id → 404; deleted/expired
transcript → 410 Gone (D16); oversized/unsupported upload → 4xx (D15/M2.5).
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)

from app.api.schemas import (
    BatchItemResult,
    BatchResponse,
    JobResponse,
    SegmentsEdit,
    SubmitResponse,
    Template,
    TemplateCreate,
)
from app.config import get_settings
from app.jobs.models import JobStatus
from app.jobs.store import JobNotFound, get_job_store
from app.languages import (
    UnknownLanguage,
    validate_language,
    validate_target_language,
)
from app.media import ffmpeg_status, validate_upload
from app.model.contract import (
    OutputFormat,
    Segment,
    TranscribeOptions,
    UnsupportedMediaError,
)
from app.output.subtitles import content_type_for, file_extension_for, render
from app.service import JobNotCompleted, get_service
from app.storage.contract import UploadTooLarge
from app.templates_store import (
    InvalidTemplateName,
    TemplateNotFound,
    get_template_store,
)
from app.templates_store import Template as StoredTemplate

router = APIRouter()


@router.get("/health", tags=["meta"], summary="Liveness check")
def health() -> dict[str, str]:
    """Return ``{"status": "ok", "ffmpeg": ...}`` when the app is up.

    Liveness, not readiness: ``status`` is ``ok`` whenever the app is up. The
    ``ffmpeg`` field (CR-016) reports whether the FFmpeg CLI — required for video
    input (D8) — is present, so a missing system dependency is visible before a
    video job fails deep in extraction. Audio still works without it (CR-011).
    """

    return {"status": "ok", "ffmpeg": ffmpeg_status().value}


def _resolve_prompt(prompt: str, template: str) -> str:
    """Resolve the effective initial_prompt (CR-022).

    An explicit ``prompt`` wins; else a ``template`` name is looked up; else empty.
    Enforces the length cap. Raises HTTPException(422) on an unknown template or an
    over-long prompt — a per-request failure, surfaced the same way as bad options.
    """

    effective = prompt.strip()
    if not effective and template.strip():
        try:
            effective = get_template_store().get(template.strip()).prompt
        except (TemplateNotFound, KeyError) as exc:
            raise HTTPException(
                status_code=422, detail=f"unknown template {template!r}"
            ) from exc
    limit = get_settings().max_prompt_chars
    if len(effective) > limit:
        raise HTTPException(
            status_code=422,
            detail=f"prompt is {len(effective)} chars; limit is {limit}",
        )
    return effective


@router.post(
    "/transcribe",
    response_model=SubmitResponse,
    status_code=202,
    tags=["transcription"],
    summary="Submit media for transcription",
    responses={
        202: {"description": "Job accepted; poll GET /jobs/{id}."},
        413: {"description": "Upload exceeds the configured size limit."},
        415: {"description": "Unsupported or unrecognised media."},
        422: {"description": "Invalid options (e.g. unknown language code)."},
    },
)
async def transcribe(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Audio or video file to transcribe."),
    language: str = Form("auto", description="'auto' or an ISO language code."),
    translate: bool = Form(False, description="Translate to English (Whisper-native)."),
    target_language: str = Form(
        "",
        description="Translate transcript into this ISO code via the MT seam "
        "(CR-025); '' = off. Not 'auto'. Independent of the English translate flag.",
    ),
    output_format: OutputFormat = Form(
        OutputFormat.text, description="text | srt | vtt | json."
    ),
    prompt: str = Form(
        "", description="Whisper initial_prompt biasing vocabulary (CR-022)."
    ),
    template: str = Form(
        "", description="Name of a saved prompt template to apply (CR-022)."
    ),
) -> SubmitResponse:
    settings = get_settings()

    # CR-028 / D28: read only the head to sniff the media kind, then STREAM the rest to
    # storage under an incremental byte cap — never buffering the whole (up-to-5 GB)
    # upload in RAM. Validate options before touching the (potentially huge) body.
    filename = file.filename or "upload"
    head = await file.read(32)
    if not head:
        raise HTTPException(status_code=415, detail="empty upload")
    try:
        validate_upload(filename, head)
    except UnsupportedMediaError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    try:
        lang = validate_language(language)
        target = validate_target_language(target_language)  # CR-025
    except UnknownLanguage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    effective_prompt = _resolve_prompt(prompt, template)  # CR-022
    options = TranscribeOptions(
        language=lang,
        translate=translate,
        output_format=output_format,
        prompt=effective_prompt,
        target_language=target,
    )
    service = get_service()
    await file.seek(0)  # rewind past the head we peeked, so the whole body is stored
    try:
        job = service.submit_stream(
            filename=filename,
            stream=file.file,  # the SpooledTemporaryFile behind UploadFile
            max_bytes=settings.max_upload_bytes,
            options=options,
        )
    except UploadTooLarge as exc:  # D15: oversized → 413, without buffering it all
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    # Async execution off the request (D11): FastAPI background task.
    background_tasks.add_task(service.execute, job.id)

    return SubmitResponse(id=job.id, status=job.status)


def _submit_one(
    *,
    filename: str,
    head: bytes,
    stream,  # noqa: ANN001 - binary file-like (UploadFile.file)
    options: TranscribeOptions,
    background_tasks: BackgroundTasks,
) -> BatchItemResult:
    """Validate + STREAM-enqueue one file for a batch (CR-019; CR-028 streaming).

    Per-file failure is returned as an ``error`` row, never raised — one bad file must
    not sink the whole batch. Sniffs from ``head`` only, then streams the body to storage
    under the byte cap: an oversized file is a ``too_large`` row (not buffered whole),
    the rest of the batch continues.
    """

    settings = get_settings()
    if not head:
        return BatchItemResult(
            filename=filename,
            error={"code": "empty_upload", "message": "empty upload"},
        )
    try:
        validate_upload(filename, head)
    except UnsupportedMediaError as exc:
        return BatchItemResult(
            filename=filename,
            error={"code": exc.code, "message": str(exc)},
        )
    service = get_service()
    try:
        job = service.submit_stream(
            filename=filename,
            stream=stream,
            max_bytes=settings.max_upload_bytes,
            options=options,
        )
    except UploadTooLarge as exc:  # D15
        return BatchItemResult(
            filename=filename,
            error={"code": "too_large", "message": str(exc)},
        )
    background_tasks.add_task(service.execute, job.id)
    return BatchItemResult(filename=filename, id=job.id, status=job.status)


@router.post(
    "/transcribe/batch",
    response_model=BatchResponse,
    tags=["transcription"],
    summary="Submit many files at once (CR-019)",
    responses={
        200: {"description": "Per-file results (accepted or rejected). Poll each id."},
        413: {"description": "Too many files in one batch (ACP_MAX_BATCH_FILES)."},
        422: {"description": "No files, or invalid options (e.g. unknown language)."},
    },
)
async def transcribe_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="Audio/video files to transcribe."),
    language: str = Form("auto", description="'auto' or an ISO language code."),
    translate: bool = Form(False, description="Translate to English (Whisper-native)."),
    target_language: str = Form(
        "",
        description="Translate every file into this ISO code via the MT seam "
        "(CR-025); '' = off. Not 'auto'.",
    ),
    output_format: OutputFormat = Form(
        OutputFormat.text,
        description="text | srt | vtt | json (applied to every file).",
    ),
    prompt: str = Form("", description="Whisper initial_prompt for every file (CR-022)."),
    template: str = Form("", description="Saved template name for every file (CR-022)."),
) -> BatchResponse:
    settings = get_settings()
    if not files:
        raise HTTPException(status_code=422, detail="no files provided")
    if len(files) > settings.max_batch_files:  # bounded fan-out
        raise HTTPException(
            status_code=413,
            detail=f"batch has {len(files)} files; limit is "
            f"{settings.max_batch_files}",
        )
    # Options are validated once for the whole batch (they apply to every file).
    try:
        lang = validate_language(language)
        target = validate_target_language(target_language)  # CR-025
    except UnknownLanguage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    effective_prompt = _resolve_prompt(prompt, template)  # CR-022
    options = TranscribeOptions(
        language=lang,
        translate=translate,
        output_format=output_format,
        prompt=effective_prompt,
        target_language=target,
    )

    results: list[BatchItemResult] = []
    for f in files:
        head = await f.read(32)  # CR-028: sniff from the head only, then stream the body
        await f.seek(0)
        results.append(
            _submit_one(
                filename=f.filename or "upload",
                head=head,
                stream=f.file,
                options=options,
                background_tasks=background_tasks,
            )
        )
    return BatchResponse(results=results)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    tags=["transcription"],
    summary="Get job status and result",
    responses={
        200: {"description": "Job found (status, and result/error when terminal)."},
        404: {"description": "No such job (unknown or malformed id)."},
        410: {"description": "Transcript deleted or expired (retention, D16)."},
    },
)
def get_job(job_id: str) -> JobResponse:
    store = get_job_store()
    try:
        job = store.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc

    rendered = None
    if job.status == JobStatus.completed and job.result is not None:
        rendered = render(job.result, job.options.output_format)
    return JobResponse.from_job(job, rendered_text=rendered)


@router.put(
    "/jobs/{job_id}/segments",
    response_model=JobResponse,
    tags=["transcription"],
    summary="Edit a completed job's subtitle segments (CR-018)",
    responses={
        200: {"description": "Segments replaced; updated job returned."},
        404: {"description": "No such job."},
        409: {"description": "Job is not completed (nothing to edit)."},
        422: {"description": "Invalid segments (empty list or end < start)."},
    },
)
def edit_segments(job_id: str, body: SegmentsEdit) -> JobResponse:
    """Replace the segments of a completed job and re-derive its text (CR-018).

    Pure data edit — no model call, no state change. Persisted durably (CR-017).
    """

    service = get_service()
    segments = [Segment(start=s.start, end=s.end, text=s.text) for s in body.segments]
    try:
        job = service.edit_segments(job_id, segments)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except JobNotCompleted as exc:
        raise HTTPException(
            status_code=409, detail=f"job is {exc}"
        ) from exc

    rendered = render(job.result, job.options.output_format) if job.result else None
    return JobResponse.from_job(job, rendered_text=rendered)


@router.get(
    "/jobs/{job_id}/subtitle",
    tags=["transcription"],
    summary="Download the rendered transcript/subtitle file",
    responses={
        200: {"description": "The rendered file (txt/srt/vtt) as a download."},
        404: {"description": "No such job."},
        409: {"description": "Job is not completed yet."},
    },
)
def download_subtitle(
    job_id: str,
    format: str | None = Query(  # noqa: A002 - matches the public query name
        None,
        description="Override the export format: txt/text, srt, or vtt. "
        "Absent → the job's original format. (CR-018)",
    ),
) -> Response:
    store = get_job_store()
    try:
        job = store.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc

    if job.status != JobStatus.completed or job.result is None:
        raise HTTPException(status_code=409, detail=f"job is {job.status.value}")

    # CR-018: honour an explicit ?format=, else fall back to the job's own format.
    # "txt" is accepted as the natural alias for the "text" enum (matches the file
    # extension callers see in the download name).
    fmt = job.options.output_format
    if format is not None:
        alias = "text" if format.lower() == "txt" else format.lower()
        try:
            fmt = OutputFormat(alias)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown format {format!r}; use txt/text, srt, or vtt",
            ) from exc
    body = render(job.result, fmt)
    ext = file_extension_for(fmt)
    return Response(
        content=body,
        media_type=content_type_for(fmt),
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}.{ext}"'
        },
    )


# --- Prompt templates CRUD (CR-022) ------------------------------------------


@router.get(
    "/templates",
    response_model=list[Template],
    tags=["templates"],
    summary="List saved prompt templates (CR-022)",
)
def list_templates() -> list[Template]:
    return [
        Template(name=t.name, prompt=t.prompt, description=t.description)
        for t in get_template_store().all()
    ]


@router.post(
    "/templates",
    response_model=Template,
    tags=["templates"],
    summary="Create or update a prompt template (CR-022)",
    responses={
        200: {"description": "Template saved."},
        422: {"description": "Invalid name or prompt too long."},
    },
)
def upsert_template(body: TemplateCreate) -> Template:
    limit = get_settings().max_prompt_chars
    if len(body.prompt) > limit:
        raise HTTPException(
            status_code=422, detail=f"prompt is {len(body.prompt)} chars; limit is {limit}"
        )
    try:
        get_template_store().upsert(
            StoredTemplate(name=body.name, prompt=body.prompt, description=body.description)
        )
    except InvalidTemplateName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Template(name=body.name, prompt=body.prompt, description=body.description)


@router.delete(
    "/templates/{name}",
    status_code=204,
    tags=["templates"],
    summary="Delete a prompt template (CR-022)",
)
def delete_template(name: str) -> Response:
    get_template_store().delete(name)  # idempotent: deleting a missing name is fine
    return Response(status_code=204)
