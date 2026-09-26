"""The real ``argos`` translator — Argos Translate, offline & MIT (CR-025 / D25).

This is the ONLY module that imports ``argostranslate``. Argos is CTranslate2-based
(already a transitive dep of faster-whisper), offline, and MIT-licensed — so it adds
arbitrary language-pair translation without reversing D4 (self-hosted, no cloud) or
D17 (MIT). Library types never cross the seam: Argos returns strings, which we map into
:class:`TranslatedResult`.

Language packages (one per direction, e.g. ``en→hi``) are downloaded/installed out of
band (``argospm`` or the demo README). A missing pair raises the typed
:class:`TranslationError`, which becomes a ``failed`` job / a stream error frame — never
a raw 500. Argos pivots through English natively when no direct package exists.

The import is lazy (inside ``__init__``) so this file can be imported for registry/type
purposes without the dependency present; the GPU-free default suite uses the fake driver.
"""

from __future__ import annotations

from app.model.contract import AUTO_LANGUAGE, Segment
from app.translate.contract import TranslatedResult, TranslationError


class ArgosTranslator:
    """Wraps Argos Translate behind the ``Translator`` contract."""

    def __init__(self) -> None:
        try:
            import argostranslate.sbd  # noqa: F401
            import argostranslate.translate  # noqa: F401
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "argostranslate is not installed. Install it (pip install "
                "argostranslate) and the needed language packages, or run with "
                "ACP_TRANSLATE_DRIVER=fake."
            ) from exc

        _neutralize_stanza_sbd()

    def translate(
        self, text: str, segments: list[Segment], *, source: str, target: str
    ) -> TranslatedResult:
        import argostranslate.translate as at

        # Resolve the source to a code Argos can actually route FROM. Whisper's
        # ``detected_language`` (what callers pass, even for "auto") can be a code Argos
        # has no from-language for — an unknown/3-letter code, or a language with no
        # installed package. Argos's ``translate(text, from, to)`` does NOT degrade
        # gracefully there: it looks the from-language up, gets ``None``, and dies with
        # ``'NoneType' object has no attribute 'get_translation'``. So we map any
        # unroutable source to **English**, which is also Argos's pivot hub (en→target is
        # the most likely installed leg). ``auto``/empty → English for the same reason.
        src = _resolve_source(at, source, target)

        # Nothing to do when the transcript is already in the target language.
        if src == target:
            return TranslatedResult(text=text, segments=list(segments), target=target)

        def _one(s: str) -> str:
            if not s:
                return s
            try:
                # Argos ≥1.9 exposes translate(text, from, to); it pivots through
                # English when no direct from→to package is installed.
                return at.translate(s, src, target)
            except Exception as exc:  # noqa: BLE001 - any library failure → typed error
                raise TranslationError(
                    f"argos could not translate {src!r}→{target!r}: {exc}"
                ) from exc

        translated_segments = [
            Segment(start=seg.start, end=seg.end, text=_one(seg.text))
            for seg in segments
        ]
        return TranslatedResult(
            text=_one(text), segments=translated_segments, target=target
        )


def _resolve_source(at, source: str, target: str) -> str:  # noqa: ANN001 - at = module
    """Return a source code Argos can route FROM (CR-025).

    ``auto``/empty → English. A concrete code is honoured **only if Argos has it as an
    installed from-language**; otherwise it falls back to English (the pivot hub), because
    Argos crashes rather than degrades on an unknown from-code (see ``translate``). English
    is the safest fallback: en→target is the leg most likely installed, and it is what every
    pivot route goes through anyway.
    """

    if not source or source == AUTO_LANGUAGE:
        return "en"
    try:
        installed = {lang.code for lang in at.get_installed_languages()}
    except Exception:  # noqa: BLE001 - if we can't enumerate, don't guess; try as-is
        return source
    return source if source in installed else "en"


_STANZA_SBD_NEUTRALIZED = False


def _neutralize_stanza_sbd() -> None:
    """Make Argos never invoke stanza for sentence boundary detection (CR-025).

    Some installed packages (e.g. ``bn→en``) declare a **stanza** sentencizer in their
    metadata, and Argos binds it by that metadata — independent of the ``chunk_type``
    setting — and caches the bound ``Translation`` internally. But stanza 1.10's
    resources have no tokenize/SBD package for several Indic languages (Bengali's entry
    lacks a ``"packages"`` key), so the first such translation dies with
    ``KeyError: 'packages'`` deep inside stanza.

    We patch ``StanzaSentencizer.split_sentences`` to delegate to Argos's built-in,
    dependency-free ``MiniSBDSentencizer``. Patching the **method on the class** (rather
    than swapping the class or clearing a cache) is what makes this robust: it also
    covers ``Translation`` objects Argos may have already constructed and cached with a
    stanza sentencizer earlier in the process. MiniSBD is language-agnostic, so this is
    safe for every pair — it only replaces the *sentence splitter*, never the
    translation model. Idempotent (guarded), so constructing multiple translators is a
    no-op after the first.
    """

    global _STANZA_SBD_NEUTRALIZED
    if _STANZA_SBD_NEUTRALIZED:
        return
    import argostranslate.sbd as sbd

    mini_cls = sbd.MiniSBDSentencizer

    def _split_via_minisbd(self, text):  # noqa: ANN001, ANN202 - matches Argos' signature
        return mini_cls(getattr(self, "pkg", None)).split_sentences(text)

    sbd.StanzaSentencizer.split_sentences = _split_via_minisbd
    _STANZA_SBD_NEUTRALIZED = True
