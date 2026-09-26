"""Config-selected driver registry (M1.1).

Feature code calls :func:`get_transcriber`; it never imports a concrete driver.
The real driver (``faster_whisper``) is the default outside tests; the ``fake``
is the test default (acp-model-seam). The real driver is imported lazily so the
GPU-free default suite never needs ``faster_whisper`` installed.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import ModelDriver, Settings, get_settings
from app.model.contract import Transcriber


def build_transcriber(settings: Settings) -> Transcriber:
    """Construct the driver named by ``settings.model_driver``."""

    if settings.model_driver == ModelDriver.fake:
        from app.model.drivers.fake import FakeTranscriber

        return FakeTranscriber()

    if settings.model_driver == ModelDriver.faster_whisper:
        # Imported lazily: the fake-only default suite must not require the
        # heavy dependency to be installed.
        from app.model.drivers.faster_whisper_driver import FasterWhisperTranscriber

        return FasterWhisperTranscriber(
            tier=settings.model_tier.value, device=settings.device.value
        )

    raise ValueError(f"Unknown model driver: {settings.model_driver!r}")


@lru_cache
def get_transcriber() -> Transcriber:
    """Process-wide transcriber singleton (loads model weights once)."""

    return build_transcriber(get_settings())


def reset_transcriber_cache() -> None:
    """Clear the cached transcriber (tests that switch drivers)."""

    get_transcriber.cache_clear()
