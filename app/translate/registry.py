"""Config-selected ``Translator`` registry (CR-025 / D25).

Feature code calls :func:`get_translator`; it never imports a concrete driver. The
real driver (``argos``) is the default outside tests; the ``fake`` is the test default
(the architecture invariant). The real driver is imported lazily so the fake-only
default suite never needs ``argostranslate`` installed.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, TranslateDriver, get_settings
from app.translate.contract import Translator


def build_translator(settings: Settings) -> Translator:
    """Construct the driver named by ``settings.translate_driver``."""

    if settings.translate_driver == TranslateDriver.fake:
        from app.translate.drivers.fake import FakeTranslator

        return FakeTranslator()

    if settings.translate_driver == TranslateDriver.argos:
        # Imported lazily: the fake-only default suite must not require the MT
        # dependency (or its language packages) to be installed.
        from app.translate.drivers.argos_driver import ArgosTranslator

        return ArgosTranslator()

    raise ValueError(f"Unknown translate driver: {settings.translate_driver!r}")


@lru_cache
def get_translator() -> Translator:
    """Process-wide translator singleton."""

    return build_translator(get_settings())


def reset_translator_cache() -> None:
    """Clear the cached translator (tests that switch drivers)."""

    get_translator.cache_clear()
