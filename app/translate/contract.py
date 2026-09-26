"""The ``Translator`` contract + DTOs (CR-025 / D25).

The one boundary between feature code and the MT library. Library types never cross
this seam — a driver maps its output into :class:`TranslatedResult`. Failure is a
typed :class:`TranslationError` (a ``failed`` job / a stream error frame), never a raw
traceback (acp-architecture: "failure is a state, not a 500").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.model.contract import Segment


class TranslationError(Exception):
    """A typed MT failure crossing the seam. Carries a stable ``code``.

    Raised e.g. when the requested source→target language-pair package is not
    installed for the real driver — surfaced as a ``failed`` job or a stream error
    frame, never a 500.
    """

    code = "translation_error"


@dataclass(frozen=True)
class TranslatedResult:
    """What a :class:`Translator` returns: the whole-text translation plus the
    per-segment translations, timing preserved (D18 grain unchanged — only text
    changes). ``target`` is the language the text is now in."""

    text: str
    segments: list[Segment] = field(default_factory=list)
    target: str = ""


@runtime_checkable
class Translator(Protocol):
    """The one method every driver implements (fake and real are parity)."""

    def translate(
        self, text: str, segments: list[Segment], *, source: str, target: str
    ) -> TranslatedResult:
        """Translate ``text`` and each segment's text from ``source`` into ``target``.

        ``source`` is the detected/used language of the transcript (may be ``""`` or
        ``auto`` — a driver treats an unknown source as "auto-detect"). ``target`` is a
        concrete ISO code (never ``auto`` — validated upstream). Segment ``start``/``end``
        are preserved; only ``text`` is translated. An uninstallable pair (or any library
        failure) raises :class:`TranslationError`.
        """
        ...
