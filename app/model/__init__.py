"""The model seam (acp-model-seam, D20).

Feature code depends on ``Transcriber`` and the DTOs here — never on
``faster_whisper`` or any concrete driver. The active driver is resolved from
config through :func:`app.model.registry.get_transcriber`. A test asserts that
``faster_whisper`` is imported nowhere outside ``app/model/drivers``.
"""

from app.model.contract import (
    OutputFormat,
    Segment,
    TranscribeOptions,
    Transcriber,
    TranscriptionError,
    TranscriptResult,
    UnsupportedMediaError,
)
from app.model.registry import get_transcriber

__all__ = [
    "Transcriber",
    "TranscribeOptions",
    "TranscriptResult",
    "Segment",
    "OutputFormat",
    "TranscriptionError",
    "UnsupportedMediaError",
    "get_transcriber",
]
