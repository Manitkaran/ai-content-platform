"""The ``Transcriber`` contract + DTOs (M1.1).

This is the one boundary between feature code and the STT library. Library types
(Whisper segments, etc.) are mapped into these DTOs and never cross the seam.

Final as-built shape recorded from
``.ai/specs/skills/acp-model-seam/references/contract.md``:

- ``TranscribeOptions.language``: ``"auto"`` | ISO code (D14)
- ``TranscribeOptions.translate``: bool — English-only, Whisper-native (D9/M3.2)
- ``TranscribeOptions.output_format``: text | srt | vtt (D9/M3.3)
- ``TranscriptResult``: text, detected_language, segments[{start,end,text}] (D18)
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class OutputFormat(str, Enum):
    """Requested transcript rendering (D9). Formatting lives in M3.3.

    ``json`` (CR-020) is the machine-readable export of the same DTO — text +
    detected language + segment-level timestamps (D18), no new content.
    """

    text = "text"
    srt = "srt"
    vtt = "vtt"
    json = "json"


# --- typed errors (acp-architecture: "failure is a state, not a 500") --------


class TranscriptionError(Exception):
    """Base for any typed failure crossing the seam. Carries a stable ``code``."""

    code = "transcription_error"


class UnsupportedMediaError(TranscriptionError):
    """The media could not be decoded (corrupt/unsupported container/codec)."""

    code = "unsupported_media"


# --- DTOs --------------------------------------------------------------------

AUTO_LANGUAGE = "auto"


@dataclass(frozen=True)
class TranscribeOptions:
    """What the caller asked for. Immutable so a job's request never mutates.

    ``language`` is ``"auto"`` (default, Whisper auto-detects, D14) or an ISO
    language code. ``translate`` requests Whisper's translate-to-English task
    (D9). ``output_format`` selects the rendering (D9/M3.3). ``prompt`` (CR-022)
    is an optional Whisper ``initial_prompt`` biasing decoding toward the caller's
    vocabulary (names/jargon); empty means none.
    """

    language: str = AUTO_LANGUAGE
    translate: bool = False
    output_format: OutputFormat = OutputFormat.text
    prompt: str = ""
    # CR-025 / D25: arbitrary source→target translation via the Translator seam, a
    # POST-transcription MT stage. Empty ("") = no MT stage (unchanged behaviour). A
    # concrete ISO code (never "auto") = translate the transcript into that language.
    # Independent of ``translate`` (Whisper-native English); the two can combine.
    target_language: str = ""


@dataclass(frozen=True)
class Segment:
    """One timestamped span of transcript (D18: segment-level only)."""

    start: float  # seconds
    end: float  # seconds
    text: str


@dataclass(frozen=True)
class TranscriptResult:
    """What a driver returns. ``segments`` back the subtitle formatters (M3.3)."""

    text: str
    detected_language: str
    segments: list[Segment] = field(default_factory=list)


# --- streaming DTOs (CR-001 / D22) -------------------------------------------


class StreamEventType(str, Enum):
    """Kinds of event a streaming transcription yields (CR-001 D-STREAM-2)."""

    partial = "partial"  # interim hypothesis; may be revised by a later event
    segment = "segment"  # a finalized, append-only timestamped span
    final = "final"  # the closing event: full text + all segments


@dataclass(frozen=True)
class StreamEvent:
    """One event emitted while streaming (CR-001).

    ``partial`` carries ``text`` (interim) and ``detected_language``.
    ``segment`` carries a finalized :class:`Segment` in ``segment``.
    ``final`` carries the full ``result``. Exactly the field for the type is set.
    """

    type: StreamEventType
    text: str = ""
    detected_language: str = ""
    segment: Segment | None = None
    result: TranscriptResult | None = None


@runtime_checkable
class Transcriber(Protocol):
    """The methods every driver implements (fake and real are parity).

    ``media_path`` is a local path to an audio file the driver can read. Video is
    converted to audio upstream (M2.5) before it reaches the seam. On unreadable
    media a driver raises :class:`UnsupportedMediaError` (a typed error), never a
    raw library traceback.
    """

    def transcribe(
        self, media_path: str, options: TranscribeOptions
    ) -> TranscriptResult: ...

    def transcribe_stream(
        self, chunks: Iterable[bytes], options: TranscribeOptions
    ) -> Iterator[StreamEvent]:
        """Transcribe a live stream of audio ``chunks`` (CR-001 / D22).

        Consumes an iterable of decoded audio-chunk bytes and yields
        :class:`StreamEvent`s — ``partial`` and ``segment`` as speech is
        recognised, then exactly one ``final`` before the iterator is exhausted.
        An undecodable stream raises :class:`UnsupportedMediaError` (a typed
        error), never a raw traceback — the WS layer turns it into an error frame
        and a clean close. Additive to :meth:`transcribe`; the file path is
        unchanged.
        """
        ...

    def transcribe_window(
        self, buffer: bytes, options: TranscribeOptions, start_time: float = 0.0
    ) -> TranscriptResult:
        """Transcribe the accumulated ``buffer`` from ``start_time`` on (CR-008 / D23;
        CR-013 / D24 added ``start_time``).

        The primitive **incremental** live streaming rides on: the WS layer calls
        this at intervals as audio arrives and emits the returned cumulative
        transcript as a ``partial``, so text appears *while the user speaks* rather
        than in a burst after ``stop`` (the D-STREAM-3 → D23 cadence reversal).
        ``start_time`` (seconds) bounds the model pass to the **new tail**
        ``buffer[start_time:]`` — the D24 fix that stops re-transcribing already-committed
        audio every window. Returned segment ``start``/``end`` are **absolute** (the
        offset is added back). ``start_time=0.0`` is the whole-buffer D23 behaviour.
        Empty ``buffer`` → an empty successful result. Undecodable audio raises
        :class:`UnsupportedMediaError`. Additive; ``transcribe``/``transcribe_stream``
        are unchanged.
        """
        ...

    def new_stream_decoder(self) -> StreamDecoder:
        """Create a **stateful** per-session decoder for live streaming (CR-027 / D27).

        The WS route owns one per connection. It exists to kill the O(n²) *decode*:
        :meth:`transcribe_window` re-decodes the whole growing buffer every window (WebM/
        Opus can't be sliced mid-stream), so decode cost climbs with session length even
        though D24 already bounded the *model* pass to the tail. The decoder decodes only
        the **newly fed** bytes into an internal PCM cache, so per-window decode is O(new)
        and the session total is O(n). Additive — the stateless
        :meth:`transcribe_window` stays for the contract/tests and non-streaming callers.
        """
        ...


@runtime_checkable
class StreamDecoder(Protocol):
    """A stateful, per-session incremental audio decoder + tail transcriber (CR-027).

    Owned by the WS route for one connection (ephemeral, D-STREAM-3 — stores nothing
    persistent). Both drivers implement it at parity: the real one holds a persistent
    codec so incremental decode is bit-exact with a full decode; the fake mirrors the
    growing-PCM contract without any codec.
    """

    def feed(self, new_bytes: bytes) -> None:
        """Decode **only** ``new_bytes`` (the chunk just received) into the internal PCM
        cache. Cheap and incremental: never re-decodes bytes fed earlier. Undecodable
        audio raises :class:`UnsupportedMediaError` (→ error frame + clean close)."""
        ...

    def transcribe_tail(
        self, options: TranscribeOptions, start_time: float = 0.0
    ) -> TranscriptResult:
        """Run the model on ``pcm[start_time:]`` of the **cached** PCM — the D24 tail-only
        pass, now with **no decode** (the PCM is already decoded by :meth:`feed`). Returned
        segment ``start``/``end`` are absolute (``start_time`` is added back). Empty/short
        tail → an empty successful result."""
        ...
