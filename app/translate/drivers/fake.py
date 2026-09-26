"""The ``fake`` translator — deterministic, no model, no download (CR-025).

The test default and the parity twin of the real Argos driver. It wraps each input
text as an **obviously synthetic** ``[MT <source>→<target>] <text>`` so a fake
translation can never be mistaken for a real one (the CR-048 lesson, applied to the
model seam's fake) and so a test can prove the MT stage ran and threaded the language
pair through without downloading a language package.

Timing is preserved; only text changes (D18 grain unchanged).
"""

from __future__ import annotations

from app.model.contract import AUTO_LANGUAGE, Segment
from app.translate.contract import TranslatedResult, TranslationError

# A marker no real translation would contain, so fake output is unmistakable.
FAKE_MARKER = "MT"

# A sentinel source/target a test can pass to force the typed-error path
# deterministically (mirrors the model fake's FAKE_BAD_CHUNK).
FAKE_BAD_TARGET = "xx-fail"


class FakeTranslator:
    """Deterministic in-process ``Translator`` (see module docstring)."""

    def translate(
        self, text: str, segments: list[Segment], *, source: str, target: str
    ) -> TranslatedResult:
        if target == FAKE_BAD_TARGET:
            raise TranslationError(f"fake: no translation package for {target!r}")

        src = source or AUTO_LANGUAGE
        tag = f"[{FAKE_MARKER} {src}→{target}]"

        def _wrap(s: str) -> str:
            return f"{tag} {s}" if s else s

        translated_segments = [
            Segment(start=seg.start, end=seg.end, text=_wrap(seg.text))
            for seg in segments
        ]
        return TranslatedResult(
            text=_wrap(text), segments=translated_segments, target=target
        )
