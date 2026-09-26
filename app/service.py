"""Transcription orchestration — the async submit→execute path (M2.2, D11).

Ties the seams together off the request:
  1. store the uploaded media through the object-storage seam (M4.1),
  2. create a ``queued`` job (M2.1),
  3. run the ``Transcriber`` seam in the background, moving the job
     ``queued → running → completed | failed`` (D12),
  4. on completion, delete the source media and set the transcript's expiry
     (retention, D16 / M4.2).

A driver or media failure becomes a ``failed`` state with a typed error — never a
crash (acp-architecture: "failure is a state, not a 500").
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import timedelta

from app.config import Settings, get_settings
from app.jobs.models import Job, JobError, JobStatus, _now
from app.jobs.store import JobStore, get_job_store
from app.media import extract_audio, sniff_kind
from app.model.contract import (
    Segment,
    TranscribeOptions,
    Transcriber,
    TranscriptionError,
    TranscriptResult,
)
from app.model.registry import get_transcriber
from app.output.subtitles import text_from_segments
from app.storage.contract import ObjectStorage
from app.storage.registry import get_storage
from app.translate.contract import TranslationError, Translator
from app.translate.registry import get_translator


class JobNotCompleted(Exception):
    """Raised when an operation needs a completed job but the job isn't (→ 409).

    Used by segment editing (CR-018): you cannot edit a transcript that does not
    exist yet (queued/running) or that failed.
    """


def _media_key(job_id: str, filename: str) -> str:
    """Storage key for a job's media (CR-028: preserves the original stem).

    The stem is kept in the key (under a per-job dir so keys never collide) so the file
    the model sees — via ``path_for``, no temp-file rewrite — still carries the original
    filename. The deterministic fake driver keys its behaviour on that stem (e.g. a
    ``silence.wav`` stem → empty result), so preserving it keeps fake/real parity across
    the storage round-trip. The stem is sanitised to a safe, single path component."""

    base = os.path.basename(filename)
    stem = os.path.splitext(base)[0] or "media"
    ext = base.rpartition(".")[2] if "." in base else "bin"
    safe_stem = "".join(c if (c.isalnum() or c in "-_") else "_" for c in stem) or "media"
    safe_ext = "".join(c if c.isalnum() else "_" for c in ext) or "bin"
    return f"media/{job_id}/{safe_stem}.{safe_ext}"


class TranscriptionService:
    """Coordinates the seams. Constructed with its dependencies for testability."""

    def __init__(
        self,
        *,
        store: JobStore,
        storage: ObjectStorage,
        transcriber: Transcriber,
        settings: Settings,
        translator: Translator,
    ) -> None:
        self._store = store
        self._storage = storage
        self._transcriber = transcriber
        self._settings = settings
        self._translator = translator

    def _maybe_translate(
        self, result: TranscriptResult, options: TranscribeOptions
    ) -> TranscriptResult:
        """Apply the MT stage when ``target_language`` is set (CR-025 / D25).

        A post-transcription pass over the same seam pattern: the transcript's text
        and every segment's text are rendered into ``options.target_language`` by the
        ``Translator`` seam. Empty target → the result is returned untouched (unchanged
        behaviour). The reported ``detected_language`` is updated to the target, since
        the transcript is now in that language. A :class:`TranslationError` from the seam
        propagates to the caller (a ``failed`` job on the file path; an error frame on the
        stream) — failure is a state, not a 500.
        """

        if not options.target_language:
            return result
        translated = self._translator.translate(
            result.text,
            result.segments,
            source=result.detected_language,
            target=options.target_language,
        )
        return TranscriptResult(
            text=translated.text,
            detected_language=options.target_language,
            segments=translated.segments,
        )

    def transcribe_stream(self, chunks, options: TranscribeOptions):  # noqa: ANN001
        """Live streaming (CR-001 / D22): delegate to the seam, store nothing.

        Ephemeral by design (D-STREAM-3) — no job row, no persisted media, no
        retention entry. Just yields the seam's :class:`StreamEvent`s. A typed
        error from the seam propagates to the WS layer, which turns it into an
        error frame and a clean close.
        """

        return self._transcriber.transcribe_stream(chunks, options)

    def transcribe_window(
        self, buffer, options: TranscribeOptions, start_time: float = 0.0
    ):  # noqa: ANN001
        """Incremental live streaming (CR-008 / D23; CR-013 / D24 added ``start_time``):
        transcribe the buffer's tail past ``start_time`` and return that tail's
        transcript. Ephemeral — stores nothing. The WS layer advances ``start_time`` as
        it commits audio, so the model never re-transcribes finalised speech.

        CR-025 / D25: if ``target_language`` is set, the tail is translated before it is
        returned, so each live ``partial``/``segment``/``final`` carries translated text.
        The MT stage runs per-window on the tail only, so the incremental cadence (D24)
        is preserved."""

        result = self._transcriber.transcribe_window(buffer, options, start_time)
        return self._maybe_translate(result, options)

    def new_stream_decoder(self):  # noqa: ANN201 - StreamDecoder
        """Create a stateful per-session stream decoder (CR-027 / D27). The WS route owns
        one per connection and drives it via :meth:`feed_and_transcribe_tail`. Ephemeral —
        stores nothing."""

        return self._transcriber.new_stream_decoder()

    def transcribe_decoder_tail(
        self, decoder, options: TranscribeOptions, start_time: float = 0.0
    ):  # noqa: ANN001
        """Run the decoder's tail-of-cached-PCM model pass and apply the MT stage (CR-027).

        The decode already happened incrementally in ``decoder.feed`` (O(new) per window),
        so this is the tail-only model pass (D24) with no re-decode, then the optional
        ``target_language`` translation (D25). Ephemeral — stores nothing."""

        result = decoder.transcribe_tail(options, start_time)
        return self._maybe_translate(result, options)

    def submit_stream(
        self, *, filename: str, stream, max_bytes: int, options: TranscribeOptions
    ) -> Job:  # noqa: ANN001 - stream is a binary file-like
        """Persist media by **streaming** it to storage under an incremental size cap,
        then create a ``queued`` job (CR-028 / D28). Never holds the whole upload in RAM.
        Raises :class:`UploadTooLarge` (→ 413) if the stream exceeds ``max_bytes``; no job
        or object is left behind on rejection. Does not execute."""

        job_id = uuid.uuid4().hex
        media_key = _media_key(job_id, filename)
        self._storage.put_stream(media_key, stream, max_bytes)  # may raise UploadTooLarge
        job = Job(
            id=job_id,
            options=options,
            media_key=media_key,
            original_filename=filename,
        )
        self._store.add(job)
        return job

    def submit(
        self, *, filename: str, data: bytes, options: TranscribeOptions
    ) -> Job:
        """Persist media, create a ``queued`` job, return it. Does not execute."""

        job_id = uuid.uuid4().hex
        media_key = _media_key(job_id, filename)
        self._storage.put(media_key, data)
        job = Job(
            id=job_id,
            options=options,
            media_key=media_key,
            original_filename=filename,
        )
        self._store.add(job)
        return job

    def execute(self, job_id: str) -> None:
        """Run one job to a terminal state. Safe to call in a background task.

        Never raises out to the caller: any failure is recorded on the job.

        CR-026: the job is claimed **atomically** (``queued → running`` in one step). The
        API background task and the worker (D11) may both call this for the same job; only
        the caller that wins the claim proceeds, so the job runs exactly once (a second run
        would fail anyway — the media is deleted on first completion, D16). A job that was
        already picked up/terminal, or deleted between submit and execute, makes ``claim``
        return ``None`` and this is a silent no-op.
        """

        job = self._store.claim(job_id)
        if job is None:
            return  # not ours: already running/terminal, or the job is gone

        tmp_path: str | None = None
        extracted_path: str | None = None
        try:
            # CR-028 / D28: prefer the on-disk path so the media never round-trips through
            # RAM. ``path_for`` is the stored file for the local driver; ``None`` for a
            # non-filesystem driver (the fake), where we fall back to get()+_write_temp.
            stored_path = self._storage.path_for(job.media_key)
            if stored_path is not None:
                source_path = stored_path
                head = _read_head(stored_path, 32)
            else:
                data = self._storage.get(job.media_key)
                tmp_path = _write_temp(data, job.original_filename)
                source_path = tmp_path
                head = data[:32]

            # Video → extract audio first (D8/M2.5); audio skips this step.
            kind = sniff_kind(head, job.original_filename)
            media_for_model = source_path
            if kind == "video":
                extracted_path = extract_audio(source_path)
                media_for_model = extracted_path

            result = self._transcriber.transcribe(media_for_model, job.options)
            # CR-025 / D25: optional MT stage — translate into target_language if asked.
            result = self._maybe_translate(result, job.options)
            job.result = result
            job.transition_to(JobStatus.completed)
            self._on_completion(job)
        except (TranscriptionError, TranslationError) as exc:
            job.error = JobError(code=exc.code, message=str(exc))
            job.transition_to(JobStatus.failed)
        except Exception as exc:  # noqa: BLE001 - defence in depth; still a state
            job.error = JobError(code="internal_error", message=str(exc))
            job.transition_to(JobStatus.failed)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                # tmp_path lives in its own temp dir (see _write_temp) — remove both.
                import shutil

                shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
            if extracted_path and os.path.exists(extracted_path):
                os.unlink(extracted_path)
            self._store.save(job)

    def edit_segments(self, job_id: str, segments: list[Segment]) -> Job:
        """Replace a completed job's segments and re-derive its text (CR-018).

        Pure data edit — no model call, no state transition (the job stays
        ``completed``). Persists via the store (durable, CR-017) and returns the
        updated job. Raises :class:`JobNotCompleted` if there is no transcript to
        edit (→ 409 at the API). Segment validity is checked by the API schema.
        """

        job = self._store.get(job_id)  # JobNotFound → 404 at the API
        if job.status != JobStatus.completed or job.result is None:
            raise JobNotCompleted(job.status.value)
        job.result = TranscriptResult(
            text=text_from_segments(segments),
            detected_language=job.result.detected_language,
            segments=segments,
        )
        job.updated_at = _now()
        self._store.save(job)
        return job

    def _on_completion(self, job: Job) -> None:
        """Retention (D16): delete source media now; keep transcript N days."""

        self._storage.delete(job.media_key)
        job.media_deleted = True
        job.expires_at = _now() + timedelta(
            days=self._settings.transcript_retention_days
        )

    def sweep_expired(self) -> int:
        """Delete transcripts past their retention window (D16). Returns count."""

        now = _now()
        removed = 0
        for job in self._store.all():
            if job.expires_at is not None and job.expires_at <= now:
                self._store.delete(job.id)
                removed += 1
        return removed


def _read_head(path: str, n: int) -> bytes:
    """Read the first ``n`` bytes of a file for magic-byte sniffing (CR-028) — so the
    worker decides audio-vs-video without reading the whole (up-to-5 GB) file into RAM."""

    with open(path, "rb") as fh:
        return fh.read(n)


def _write_temp(data: bytes, filename: str) -> str:
    """Write ``data`` to a temp file whose name preserves the original stem.

    Preserving the stem keeps the deterministic fake driver's behaviour stable
    across the storage round-trip (e.g. a ``silence.wav`` upload still yields the
    empty-result path); the real driver ignores the name entirely.
    """

    stem = os.path.splitext(os.path.basename(filename))[0] or "media"
    suffix = "." + filename.rpartition(".")[2] if "." in filename else ""
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, f"{stem}{suffix}")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def get_service() -> TranscriptionService:
    """Build a service from the process-wide seams (used by the API layer)."""

    return TranscriptionService(
        store=get_job_store(),
        storage=get_storage(),
        transcriber=get_transcriber(),
        settings=get_settings(),
        translator=get_translator(),
    )
