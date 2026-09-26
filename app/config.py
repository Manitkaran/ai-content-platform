"""The single configuration layer — the ONLY place env vars are read.

Convention (acp-architecture, "config is the only env surface"): nothing else in
the codebase calls ``os.environ`` / ``os.getenv``. A test (``tests/test_config.py``)
enforces that, and a drift test keeps ``.env.example`` and this module in sync in
both directions.

Every enumerable value is a typed enum (acp-architecture, "types over strings").
A missing *required* key fails loudly at import/startup, not at first use.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache

# The one sanctioned read of the environment. Every key below is documented in
# ``.env.example``; ``ENV_KEYS`` is the source of truth the drift test checks.


class Environment(str, Enum):
    """Runtime environment. ``testing`` unlocks test-only driver defaults."""

    development = "development"
    testing = "testing"
    production = "production"


class ModelDriver(str, Enum):
    """Which ``Transcriber`` implementation the model seam resolves (D20)."""

    fake = "fake"
    faster_whisper = "faster_whisper"


class StorageDriver(str, Enum):
    """Which ``ObjectStorage`` implementation the storage seam resolves (D21)."""

    local = "local"
    fake = "fake"  # test-only; never a production default (CR-048 lesson)


class TranslateDriver(str, Enum):
    """Which ``Translator`` implementation the translate seam resolves (CR-025 / D25).

    ``argos`` is Argos Translate (offline, MIT) — the real default; ``fake`` is the
    deterministic test default (no model, no download)."""

    argos = "argos"
    fake = "fake"


class ModelTier(str, Enum):
    """Whisper model size (D13). Smaller = faster/cheaper, larger = better."""

    tiny = "tiny"
    base = "base"
    small = "small"
    medium = "medium"
    large_v3 = "large-v3"


class Device(str, Enum):
    """Inference device (D13). CPU-first; GPU is an opt-in override."""

    cpu = "cpu"
    cuda = "cuda"


# --- key registry -----------------------------------------------------------
# Name -> (default, required). ``required`` keys have no default and must be set;
# absence raises at construction. This registry is what the drift test compares
# against ``.env.example`` so neither can silently gain or lose a key.

ENV_KEYS: dict[str, tuple[str | None, bool]] = {
    "ACP_APP_NAME": ("ai-content-platform", False),
    "ACP_ENV": (Environment.development.value, False),
    "ACP_MODEL_DRIVER": (ModelDriver.faster_whisper.value, False),
    "ACP_MODEL_TIER": (ModelTier.small.value, False),  # CR-014: better on accents/names
    "ACP_DEVICE": (Device.cpu.value, False),
    "ACP_STORAGE_DRIVER": (StorageDriver.local.value, False),
    "ACP_TRANSLATE_DRIVER": (TranslateDriver.argos.value, False),  # CR-025: MT seam
    "ACP_DATA_DIR": ("./data", False),
    "ACP_DATABASE_URL": ("sqlite:///./data/acp.db", False),
    "ACP_MAX_UPLOAD_BYTES": (str(5 * 1024 * 1024 * 1024), False),  # D15; CR-021: 5 GB
    "ACP_MAX_BATCH_FILES": ("20", False),  # CR-019: max files per /transcribe/batch
    "ACP_MAX_PROMPT_CHARS": ("2000", False),  # CR-022: max initial_prompt length
    "ACP_TRANSCRIPT_RETENTION_DAYS": ("30", False),  # D16: transcript kept N days
    "ACP_HOST": ("0.0.0.0", False),
    "ACP_PORT": ("8000", False),
}


class ConfigError(RuntimeError):
    """Raised at startup for a missing required key or an illegal value."""


class Settings:
    """Typed, validated settings. Constructed once; read everywhere.

    Reads only from ``env`` (defaults to ``os.environ``). Constructing with an
    explicit dict is how tests supply an isolated environment without touching
    the process env.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        src = os.environ if env is None else env

        def read(key: str) -> str:
            default, required = ENV_KEYS[key]
            if key in src and src[key] != "":
                return src[key]
            if required:
                raise ConfigError(
                    f"Required environment variable {key!r} is not set. "
                    f"See .env.example."
                )
            assert default is not None  # non-required keys always have a default
            return default

        self.app_name: str = read("ACP_APP_NAME")
        self.environment: Environment = _coerce(Environment, read("ACP_ENV"), "ACP_ENV")
        self.model_driver: ModelDriver = _coerce(
            ModelDriver, read("ACP_MODEL_DRIVER"), "ACP_MODEL_DRIVER"
        )
        self.model_tier: ModelTier = _coerce(
            ModelTier, read("ACP_MODEL_TIER"), "ACP_MODEL_TIER"
        )
        self.device: Device = _coerce(Device, read("ACP_DEVICE"), "ACP_DEVICE")
        self.storage_driver: StorageDriver = _coerce(
            StorageDriver, read("ACP_STORAGE_DRIVER"), "ACP_STORAGE_DRIVER"
        )
        self.translate_driver: TranslateDriver = _coerce(
            TranslateDriver, read("ACP_TRANSLATE_DRIVER"), "ACP_TRANSLATE_DRIVER"
        )
        self.data_dir: str = read("ACP_DATA_DIR")
        self.database_url: str = read("ACP_DATABASE_URL")
        self.max_upload_bytes: int = _coerce_int(
            read("ACP_MAX_UPLOAD_BYTES"), "ACP_MAX_UPLOAD_BYTES"
        )
        self.max_batch_files: int = _coerce_int(
            read("ACP_MAX_BATCH_FILES"), "ACP_MAX_BATCH_FILES"
        )
        self.max_prompt_chars: int = _coerce_int(
            read("ACP_MAX_PROMPT_CHARS"), "ACP_MAX_PROMPT_CHARS"
        )
        self.transcript_retention_days: int = _coerce_int(
            read("ACP_TRANSCRIPT_RETENTION_DAYS"), "ACP_TRANSCRIPT_RETENTION_DAYS"
        )
        self.host: str = read("ACP_HOST")
        self.port: int = _coerce_int(read("ACP_PORT"), "ACP_PORT")

        self._validate()

    def _validate(self) -> None:
        # A fake object store in production is the CR-048 trap (D21). Refuse it.
        if (
            self.environment == Environment.production
            and self.storage_driver == StorageDriver.fake
        ):
            raise ConfigError(
                "ACP_STORAGE_DRIVER=fake is test-only and must never run in "
                "production (data would be silently discarded)."
            )

    @property
    def is_testing(self) -> bool:
        return self.environment == Environment.testing


def _coerce(enum_cls: type[Enum], value: str, key: str) -> Enum:
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ConfigError(
            f"Invalid value {value!r} for {key}. Allowed: {allowed}."
        ) from exc


def _coerce_int(value: str, key: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {value!r}.") from exc


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton (reads ``os.environ`` once)."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the cache so tests can re-read a changed environment."""

    get_settings.cache_clear()
