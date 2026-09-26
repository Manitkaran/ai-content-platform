"""Language code validation for the ``language`` option (M3.1, D14).

``auto`` (default) means Whisper auto-detects. An explicit code must be a known
Whisper language code, else the API rejects it with a 4xx. The result always
surfaces the detected/used language (carried on ``TranscriptResult``).
"""

from __future__ import annotations

from app.model.contract import AUTO_LANGUAGE

# Whisper's supported language codes (ISO 639-1, a representative superset that
# includes Hindi/English/Bengali per D14). Kept as a frozenset for O(1) checks.
WHISPER_LANGUAGES: frozenset[str] = frozenset(
    """
    af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo
    fr gl gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo
    lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si
    sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue
    """.split()
)


class UnknownLanguage(ValueError):
    """The caller supplied a language code Whisper does not support (→ 4xx)."""


def validate_language(language: str) -> str:
    """Return the normalised language, or raise :class:`UnknownLanguage`."""

    if language is None or language == "":
        return AUTO_LANGUAGE
    normalised = language.strip().lower()
    if normalised == AUTO_LANGUAGE:
        return AUTO_LANGUAGE
    if normalised not in WHISPER_LANGUAGES:
        raise UnknownLanguage(
            f"unknown language code {language!r}; use 'auto' or an ISO code "
            f"such as en, es, hi"
        )
    return normalised


def validate_target_language(language: str) -> str:
    """Validate a ``target_language`` for the MT stage (CR-025 / D25).

    Empty/``None`` → ``""`` (no translation stage). Unlike :func:`validate_language`,
    ``auto`` is **not** valid: auto-detect is a *source* concept (D14) — a translation
    target must name where you're going. Any other value must be a known code, else
    :class:`UnknownLanguage` (→ 422)."""

    if language is None or language.strip() == "":
        return ""
    normalised = language.strip().lower()
    if normalised == AUTO_LANGUAGE:
        raise UnknownLanguage(
            "'auto' is not a valid target_language; name a concrete code such as "
            "en, hi, bn"
        )
    if normalised not in WHISPER_LANGUAGES:
        raise UnknownLanguage(
            f"unknown target_language {language!r}; use an ISO code such as en, hi, bn"
        )
    return normalised
