"""M0.1 — config layer + .env.example drift test."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import (
    ENV_KEYS,
    ConfigError,
    Environment,
    Settings,
    StorageDriver,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _keys_in_env_example() -> set[str]:
    text = ENV_EXAMPLE.read_text()
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def test_env_example_documents_every_key_and_no_more():
    """Drift in either direction is a defect (M0.1 acceptance)."""

    documented = _keys_in_env_example()
    declared = set(ENV_KEYS)
    assert documented == declared, (
        f"missing from .env.example: {declared - documented}; "
        f"extra in .env.example: {documented - declared}"
    )


def test_defaults_load():
    s = Settings(env={})
    assert s.app_name == "ai-content-platform"
    assert s.environment == Environment.development
    assert s.max_upload_bytes == 5 * 1024 * 1024 * 1024  # CR-021: 5 GB default


def test_default_model_tier_is_small():
    # CR-014: default tier is `small` (better on accented English + non-English names
    # than `base`, still CPU-workable). Overridable via ACP_MODEL_TIER.
    from app.config import ModelTier

    assert Settings(env={}).model_tier == ModelTier.small


def test_invalid_enum_fails_loudly():
    with pytest.raises(ConfigError):
        Settings(env={"ACP_ENV": "nonsense"})


def test_fake_storage_refused_in_production():
    with pytest.raises(ConfigError):
        Settings(env={"ACP_ENV": "production", "ACP_STORAGE_DRIVER": "fake"})
    # but allowed outside production
    s = Settings(env={"ACP_ENV": "testing", "ACP_STORAGE_DRIVER": "fake"})
    assert s.storage_driver == StorageDriver.fake


def test_bad_integer_fails_loudly():
    with pytest.raises(ConfigError):
        Settings(env={"ACP_MAX_UPLOAD_BYTES": "not-a-number"})
