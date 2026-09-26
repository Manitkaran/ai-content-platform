"""WebSocket live-streaming transcription — ``WS /transcribe/stream`` (CR-001 / D22).

A thin transport over the model seam's :meth:`Transcriber.transcribe_stream`. The
wire protocol (D-STREAM-2) is:

  client → server (text, first):  {"type":"start","language":"auto","translate":false}
  client → server (binary):       raw audio-chunk frames (one frame = one chunk)
  client → server (text, last):   {"type":"stop"}

  server → client (text):         {"type":"partial","text":…,"detected_language":…}
                                  {"type":"segment","start":…,"end":…,"text":…}
                                  {"type":"final","text":…,"detected_language":…,"segments":[…]}
                                  {"type":"error","code":…,"message":…}

The session is **ephemeral** (D-STREAM-3): no job row, no stored audio, no retention
entry. Failure is a typed error frame + a clean close, never an unhandled 500
(acp-architecture: "failure is a state, not a 500"). Auth is unchanged — single
operator on a trusted network (D10).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.languages import (
    UnknownLanguage,
    validate_language,
    validate_target_language,
)
from app.model.contract import (
    OutputFormat,
    StreamEvent,
    StreamEventType,
    TranscribeOptions,
    TranscriptionError,
    TranscriptResult,
)
from app.service import get_service
from app.translate.contract import TranslationError

stream_router = APIRouter()


@stream_router.websocket("/transcribe/stream")
async def transcribe_stream(ws: WebSocket) -> None:
    """Drive one live streaming session over the model seam (CR-001)."""

    await ws.accept()
    try:
        options = await _read_start(ws)
    except _ProtocolError as exc:
        await _send_error(ws, exc.code, str(exc))
        await ws.close()
        return

    # CR-013 / D24: INCREMENTAL streaming — keep a committed offset (seconds finalised)
    # and committed text; each window transcribes only the NEW tail past that offset.
    # CR-027 / D27: a stateful per-session DECODER decodes only newly-arrived bytes into a
    # PCM cache (killing the O(n²) whole-buffer re-decode); the window/final passes model
    # the cached tail with no re-decode. Off-thread (CR-010) with a ``busy`` guard.
    service = get_service()
    decoder = service.new_stream_decoder()
    since_window = 0
    busy = False
    state = _StreamState()   # committed_secs, committed_text, last_partial
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                return  # client vanished; nothing to finalise, session is ephemeral
            if (data := message.get("bytes")) is not None:
                # Decode the new bytes now (off-thread) so the PCM cache grows as audio
                # arrives; the model pass on a window just slices the cache (no decode).
                await asyncio.to_thread(decoder.feed, data)
                since_window += 1
                if since_window >= _WINDOW_CHUNKS and not busy:
                    since_window = 0
                    busy = True
                    try:
                        await _emit_window(ws, service, decoder, options, state)
                    finally:
                        busy = False
                continue
            text = message.get("text")
            if text is not None and _is_stop(text):
                break
    except WebSocketDisconnect:
        return
    except (TranscriptionError, TranslationError) as exc:  # CR-025: MT failure is typed
        await _send_error(ws, exc.code, str(exc))
        await ws.close()
        return
    except Exception as exc:  # noqa: BLE001 - still a clean close, never a raw 500
        await _send_error(ws, "internal_error", str(exc))
        await ws.close()
        return

    # ``stop`` — transcribe the remaining tail, commit it all, emit the finalized
    # ``segment`` frames (D-STREAM-2), then the terminal ``final`` (committed + tail).
    try:
        tail = await asyncio.to_thread(
            service.transcribe_decoder_tail, decoder, options, state.committed_secs
        )
        for seg in tail.segments:
            await _send_event(ws, StreamEvent(type=StreamEventType.segment, segment=seg))
        full = (state.committed_text + " " + tail.text).strip()
        result = TranscriptResult(
            text=full,
            detected_language=tail.detected_language or "",
            segments=tail.segments,
        )
        await _send_event(ws, StreamEvent(type=StreamEventType.final, result=result))
    except (TranscriptionError, TranslationError) as exc:  # CR-025: MT failure is typed
        await _send_error(ws, exc.code, str(exc))
    except Exception as exc:  # noqa: BLE001 - still a clean close, never a raw 500
        await _send_error(ws, "internal_error", str(exc))
    finally:
        await ws.close()


class _StreamState:
    """CR-013 / D24: per-session incremental streaming state."""

    def __init__(self) -> None:
        self.committed_secs = 0.0   # audio finalised; the model never re-reads before it
        self.committed_text = ""    # transcript of the committed audio
        self.last_partial = ""      # last emitted cumulative partial (dedupe)


# CR-013 / D24: transcribe only the tail past ``state.committed_secs`` (off-thread,
# CR-010), commit segments that end before the live edge, and emit a ``partial`` of
# committed + still-changing tail. Bounds the MODEL pass to NEW audio. CR-027 / D27: the
# tail comes from the decoder's pre-decoded PCM cache (no re-decode), so the DECODE is
# bounded too — per-update cost is finally constant, not growing with recording length.
async def _emit_window(ws, service, decoder, options, state):  # noqa: ANN001
    tail = await asyncio.to_thread(
        service.transcribe_decoder_tail, decoder, options, state.committed_secs
    )
    if not tail.segments and not tail.text:
        return

    # Commit every segment that ends comfortably before the live edge; the last,
    # still-changing segment stays as the live tail.
    live_edge = max((s.end for s in tail.segments), default=state.committed_secs)
    tail_words: list[str] = []
    for seg in tail.segments:
        if seg.end <= live_edge - _COMMIT_GAP_SECS:
            state.committed_text = (state.committed_text + " " + seg.text).strip()
            state.committed_secs = max(state.committed_secs, seg.end)
        else:
            tail_words.append(seg.text)

    cumulative = (state.committed_text + " " + " ".join(tail_words)).strip()
    if cumulative and cumulative != state.last_partial:
        await _send_event(
            ws,
            StreamEvent(
                type=StreamEventType.partial,
                text=cumulative,
                detected_language=tail.detected_language,
            ),
        )
        state.last_partial = cumulative


# Received audio frames per window (~250ms/chunk, CR-009 → ~1.5s window).
_WINDOW_CHUNKS = 6
# A segment must end this far behind the live edge before it is committed (never
# re-transcribed) — leaves the trailing, still-forming speech revisable.
_COMMIT_GAP_SECS = 1.0


class _ProtocolError(Exception):
    """A malformed ``start`` frame. Carries a stable ``code`` for the error frame."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def _read_start(ws: WebSocket) -> TranscribeOptions:
    """Read and validate the opening ``start`` frame → :class:`TranscribeOptions`."""

    try:
        message = await ws.receive_json()
    except (ValueError, KeyError, TypeError) as exc:
        raise _ProtocolError("bad_start", "first frame must be JSON 'start'") from exc

    if not isinstance(message, dict) or message.get("type") != "start":
        raise _ProtocolError("bad_start", "expected a 'start' message first")

    language_raw = message.get("language", "auto")
    try:
        language = validate_language(str(language_raw))
    except UnknownLanguage as exc:
        raise _ProtocolError("invalid_language", str(exc)) from exc

    translate = bool(message.get("translate", False))

    # CR-025 / D25: optional arbitrary-target translation. "" (or absent) = no MT stage.
    try:
        target_language = validate_target_language(str(message.get("target_language", "")))
    except UnknownLanguage as exc:
        raise _ProtocolError("invalid_language", str(exc)) from exc

    # output_format is not offered for streaming (segments are delivered structured).
    return TranscribeOptions(
        language=language,
        translate=translate,
        output_format=OutputFormat.text,
        target_language=target_language,
    )


def _is_stop(text: str) -> bool:
    import json

    try:
        payload = json.loads(text)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "stop"


async def _send_event(ws: WebSocket, event) -> None:  # noqa: ANN001 - StreamEvent
    """Serialise a :class:`StreamEvent` to its wire frame (D-STREAM-2)."""

    if event.type is StreamEventType.partial:
        await ws.send_json(
            {
                "type": "partial",
                "text": event.text,
                "detected_language": event.detected_language,
            }
        )
    elif event.type is StreamEventType.segment:
        seg = event.segment
        await ws.send_json(
            {"type": "segment", "start": seg.start, "end": seg.end, "text": seg.text}
        )
    elif event.type is StreamEventType.final:
        result = event.result
        await ws.send_json(
            {
                "type": "final",
                "text": result.text,
                "detected_language": result.detected_language,
                "segments": [asdict(s) for s in result.segments],
            }
        )


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    await ws.send_json({"type": "error", "code": code, "message": message})
