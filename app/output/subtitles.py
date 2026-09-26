"""Pure subtitle formatters over transcript segments (M3.3, D18).

No model dependency — this is a formatting step over the segments the seam
already returns. Segment-level timestamps only (D18).
"""

from __future__ import annotations

import json

from app.model.contract import OutputFormat, Segment, TranscriptResult


def _format_timestamp(seconds: float, *, comma: bool) -> str:
    """``HH:MM:SS,mmm`` (SRT) or ``HH:MM:SS.mmm`` (VTT)."""

    if seconds < 0:
        seconds = 0.0
    millis_total = int(round(seconds * 1000))
    hours, millis_total = divmod(millis_total, 3_600_000)
    minutes, millis_total = divmod(millis_total, 60_000)
    secs, millis = divmod(millis_total, 1000)
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def to_srt(segments: list[Segment]) -> str:
    """Render segments as a valid SRT document."""

    blocks = []
    for i, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg.start, comma=True)
        end = _format_timestamp(seg.end, comma=True)
        blocks.append(f"{i}\n{start} --> {end}\n{seg.text.strip()}\n")
    return "\n".join(blocks)


def to_vtt(segments: list[Segment]) -> str:
    """Render segments as a valid WebVTT document."""

    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _format_timestamp(seg.start, comma=False)
        end = _format_timestamp(seg.end, comma=False)
        lines.append(f"{start} --> {end}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def text_from_segments(segments: list[Segment]) -> str:
    """Join segment texts into the flat transcript (CR-018).

    Used after an edit so ``result.text`` (and TXT export) tracks the corrected
    segments. Mirrors the plain-transcript shape the drivers produce: one space
    between trimmed segment texts.
    """

    return " ".join(seg.text.strip() for seg in segments if seg.text.strip())


def to_json(result: TranscriptResult) -> str:
    """Render the full result as a JSON document (CR-020).

    The machine-readable export of the same DTO — text, detected language, and
    segment-level timestamps (D18). Pretty-printed; non-ASCII kept verbatim so
    e.g. Hindi/Bengali transcripts are readable.
    """

    return json.dumps(
        {
            "text": result.text,
            "detected_language": result.detected_language,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in result.segments
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


def render(result: TranscriptResult, output_format: OutputFormat) -> str:
    """Render a result in the requested format. ``text`` is the plain transcript."""

    if output_format == OutputFormat.text:
        return result.text
    if output_format == OutputFormat.srt:
        return to_srt(result.segments)
    if output_format == OutputFormat.vtt:
        return to_vtt(result.segments)
    if output_format == OutputFormat.json:
        return to_json(result)
    raise ValueError(f"unknown output format: {output_format!r}")


def content_type_for(output_format: OutputFormat) -> str:
    return {
        OutputFormat.text: "text/plain; charset=utf-8",
        OutputFormat.srt: "application/x-subrip; charset=utf-8",
        OutputFormat.vtt: "text/vtt; charset=utf-8",
        OutputFormat.json: "application/json; charset=utf-8",
    }[output_format]


def file_extension_for(output_format: OutputFormat) -> str:
    return {
        OutputFormat.text: "txt",
        OutputFormat.srt: "srt",
        OutputFormat.vtt: "vtt",
        OutputFormat.json: "json",
    }[output_format]
