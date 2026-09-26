"""The ``fake`` transcriber — deterministic, no model, no GPU (M1.2).

The test default. It returns an **obviously synthetic** transcript derived from
the input, so a fake result can never be mistaken for a real one (the CR-048
lesson). Given the same input it returns the same result; change the input and
the result changes deterministically.

An empty/whitespace filename stem yields empty text — this lets M4.3's
"silent clip → completed with empty text" path be exercised on the fake by
naming a fixture ``silence.wav`` won't do it, but an intentionally-empty stem
will; the real driver handles genuine silence.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator

from app.model.contract import (
    AUTO_LANGUAGE,
    Segment,
    StreamEvent,
    StreamEventType,
    TranscribeOptions,
    TranscriptResult,
    UnsupportedMediaError,
)

# A marker no real transcript would contain, so fake output is unmistakable.
FAKE_MARKER = "[FAKE-TRANSCRIBER]"

# A sentinel a caller/test can feed as an audio chunk to force the typed
# "undecodable stream" path deterministically, without needing FFmpeg or a real
# corrupt byte stream (CR-001 acceptance test 4). No real audio contains it.
FAKE_BAD_CHUNK = b"__ACP_FAKE_BAD_AUDIO__"


class FakeTranscriber:
    """Deterministic in-process ``Transcriber`` (see module docstring)."""

    def transcribe(
        self, media_path: str, options: TranscribeOptions
    ) -> TranscriptResult:
        # Mirror the real driver's typed failure on a missing/unreadable file, so
        # error-path tests behave identically on both drivers.
        if not os.path.exists(media_path):
            raise UnsupportedMediaError(f"fake: no such media file: {media_path}")

        stem = os.path.splitext(os.path.basename(media_path))[0]

        # A stem signalling "no speech" produces an empty-but-successful result
        # (feeds M4.3's empty-result path). Otherwise, deterministic words.
        if stem.strip().lower() in {"", "silence", "empty"}:
            detected = _detected_language(options, media_path)
            return TranscriptResult(text="", detected_language=detected, segments=[])

        detected = _detected_language(options, media_path)
        words = _deterministic_words(media_path)
        # If translation was requested, echo it so downstream wiring is provable
        # without doing real MT work.
        prefix = "translated " if options.translate else ""
        # CR-022: echo the prompt so a test can prove the initial_prompt threaded
        # through the seam without a real model. Empty prompt → unchanged output.
        if options.prompt:
            prefix = f"{prefix}[prompt:{options.prompt}] "
        segments = [
            Segment(
                start=float(i),
                end=float(i) + 1.0,
                text=f"{FAKE_MARKER} {prefix}{word}",
            )
            for i, word in enumerate(words)
        ]
        text = " ".join(seg.text for seg in segments)
        return TranscriptResult(text=text, detected_language=detected, segments=segments)

    def transcribe_window(
        self, buffer: bytes, options: TranscribeOptions, start_time: float = 0.0
    ) -> TranscriptResult:
        """Transcribe the accumulated buffer from ``start_time`` on (CR-008 / D23;
        CR-013 / D24 added ``start_time``).

        Deterministic, and the word count **grows with the buffer length**, so re-running
        on a longer buffer yields a longer transcript. ``start_time`` (seconds) drops the
        words before it — the incremental "new tail only" contract, testable without a
        model (a later offset → fewer words, timestamps ≥ offset). Empty buffer → empty;
        the ``FAKE_BAD_CHUNK`` sentinel raises the typed error.
        """

        if buffer.startswith(FAKE_BAD_CHUNK) or FAKE_BAD_CHUNK in buffer:
            raise UnsupportedMediaError("fake: undecodable audio stream")

        detected = _detected_language_for_bytes(options, buffer)
        if not buffer:
            return TranscriptResult(text="", detected_language=detected, segments=[])

        prefix = "translated " if options.translate else ""
        words = _windowed_words_for_bytes(buffer)
        # One word per second at index i; keep only the tail past start_time (D24).
        segments = [
            Segment(start=float(i), end=float(i) + 1.0, text=f"{FAKE_MARKER} {prefix}{w}")
            for i, w in enumerate(words)
            if float(i) >= start_time
        ]
        text = " ".join(seg.text for seg in segments)
        return TranscriptResult(
            text=text, detected_language=detected, segments=segments
        )

    def transcribe_stream(
        self, chunks: Iterable[bytes], options: TranscribeOptions
    ) -> Iterator[StreamEvent]:
        """Deterministic streaming (CR-001). Words derive from the audio bytes.

        Consumes ``chunks`` (already-decoded audio bytes from the WS layer),
        emits a ``partial`` then a finalized ``segment`` per word, then exactly
        one ``final``. No audio → an empty but successful ``final`` (mirrors the
        file path's empty-clip rule). A :data:`FAKE_BAD_CHUNK` sentinel raises
        :class:`UnsupportedMediaError`, exercising the typed-error path.
        """

        buffer = bytearray()
        for chunk in chunks:
            if chunk.startswith(FAKE_BAD_CHUNK):
                raise UnsupportedMediaError("fake: undecodable audio stream")
            buffer.extend(chunk)

        # Language: honour an explicit override; otherwise derive deterministically
        # from the audio bytes so `auto` still reports something stable.
        seed = bytes(buffer)
        detected = _detected_language_for_bytes(options, seed)
        prefix = "translated " if options.translate else ""

        if not buffer:
            yield StreamEvent(
                type=StreamEventType.final,
                result=TranscriptResult(
                    text="", detected_language=detected, segments=[]
                ),
            )
            return

        words = _deterministic_words_for_bytes(seed)
        segments: list[Segment] = []
        for i, word in enumerate(words):
            text = f"{FAKE_MARKER} {prefix}{word}"
            # An interim hypothesis first (may be revised) …
            yield StreamEvent(
                type=StreamEventType.partial, text=text, detected_language=detected
            )
            seg = Segment(start=float(i), end=float(i) + 1.0, text=text)
            segments.append(seg)
            # … then the finalized, append-only segment.
            yield StreamEvent(type=StreamEventType.segment, segment=seg)

        full = " ".join(seg.text for seg in segments)
        yield StreamEvent(
            type=StreamEventType.final,
            result=TranscriptResult(
                text=full, detected_language=detected, segments=segments
            ),
        )

    def new_stream_decoder(self) -> FakeStreamDecoder:
        """The stateful per-session decoder (CR-027). Deterministic, no codec."""

        return FakeStreamDecoder()


class FakeStreamDecoder:
    """Deterministic stateful stream decoder (CR-027), the parity twin of the real one.

    ``feed`` accumulates the fed bytes (and counts how many bytes it has *decoded*, so a
    test can prove decode is incremental — each byte is decoded exactly once across the
    session, never re-decoded). ``transcribe_tail`` derives the same growing word list
    from the accumulated bytes that :meth:`FakeTranscriber.transcribe_window` would, so the
    two paths agree. The ``FAKE_BAD_CHUNK`` sentinel still trips the typed error, at feed
    time (mirroring the real decoder failing on undecodable audio as it arrives).
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.decoded_bytes = 0  # test hook: total bytes ever decoded (must == len fed)

    def feed(self, new_bytes: bytes) -> None:
        if FAKE_BAD_CHUNK in new_bytes:
            raise UnsupportedMediaError("fake: undecodable audio stream")
        # Incremental: we only ever "decode" the NEW bytes, never re-decode the prefix.
        self.decoded_bytes += len(new_bytes)
        self._buffer.extend(new_bytes)

    def transcribe_tail(
        self, options: TranscribeOptions, start_time: float = 0.0
    ) -> TranscriptResult:
        data = bytes(self._buffer)
        detected = _detected_language_for_bytes(options, data)
        if not data:
            return TranscriptResult(text="", detected_language=detected, segments=[])
        prefix = "translated " if options.translate else ""
        words = _windowed_words_for_bytes(data)
        segments = [
            Segment(start=float(i), end=float(i) + 1.0, text=f"{FAKE_MARKER} {prefix}{w}")
            for i, w in enumerate(words)
            if float(i) >= start_time
        ]
        text = " ".join(seg.text for seg in segments)
        return TranscriptResult(text=text, detected_language=detected, segments=segments)


def _detected_language(options: TranscribeOptions, media_path: str) -> str:
    """Honour an explicit language; otherwise a deterministic stub 'detection'."""

    if options.language and options.language != AUTO_LANGUAGE:
        return options.language
    # Deterministic pseudo-detection so `auto` still reports *something* stable.
    digest = hashlib.sha256(media_path.encode()).hexdigest()
    return {0: "en", 1: "es", 2: "hi", 3: "fr"}[int(digest[0], 16) % 4]


def _deterministic_words(media_path: str) -> list[str]:
    """A short, input-derived word list. Same input → same words."""

    digest = hashlib.sha256(media_path.encode()).hexdigest()
    vocabulary = [
        "hello",
        "world",
        "this",
        "is",
        "a",
        "fake",
        "transcript",
        "for",
        "testing",
        "only",
    ]
    count = 3 + (int(digest[:2], 16) % 4)  # 3..6 words, deterministic
    return [vocabulary[int(digest[i], 16) % len(vocabulary)] for i in range(count)]


def _detected_language_for_bytes(options: TranscribeOptions, data: bytes) -> str:
    """Streaming twin of :func:`_detected_language`, keyed on the audio bytes."""

    if options.language and options.language != AUTO_LANGUAGE:
        return options.language
    digest = hashlib.sha256(data).hexdigest()
    return {0: "en", 1: "es", 2: "hi", 3: "fr"}[int(digest[0], 16) % 4]


def _deterministic_words_for_bytes(data: bytes) -> list[str]:
    """Streaming twin of :func:`_deterministic_words`, keyed on the audio bytes."""

    digest = hashlib.sha256(data).hexdigest()
    vocabulary = [
        "hello",
        "world",
        "this",
        "is",
        "a",
        "fake",
        "transcript",
        "for",
        "testing",
        "only",
    ]
    count = 3 + (int(digest[:2], 16) % 4)  # 3..6 words, deterministic
    return [vocabulary[int(digest[i], 16) % len(vocabulary)] for i in range(count)]


def _windowed_words_for_bytes(data: bytes) -> list[str]:
    """Like :func:`_deterministic_words_for_bytes` but the word count **grows with
    the buffer length** (CR-008 / D23) — ~one word per 8 bytes, so re-running on a
    longer accumulated buffer returns a longer transcript. Deterministic in the
    bytes; the demo just needs the transcript to *grow* as audio arrives."""

    digest = hashlib.sha256(data).hexdigest()
    vocabulary = [
        "hello", "world", "this", "is", "a", "fake",
        "transcript", "for", "testing", "only",
    ]
    count = max(1, min(60, len(data) // 8))  # ~1 word per 8 bytes, capped
    return [
        vocabulary[int(digest[i % len(digest)], 16) % len(vocabulary)]
        for i in range(count)
    ]
