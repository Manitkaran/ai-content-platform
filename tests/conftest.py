"""Test harness (M0.3).

Enforces the GPU-free default suite: the model driver defaults to ``fake`` and
the environment to ``testing`` for every test. Each test gets fresh, isolated
seams (a clean job store, a temp data dir) so no state leaks between tests —
mirroring the platform's ``*_testing`` datastore-isolation rule.

Real-model tests are marked ``@pytest.mark.real_model`` and are deselected from
the default run (see ``pytest.ini``).
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Set a testing environment with fake drivers and a temp data dir."""

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env = {
        "ACP_ENV": "testing",
        "ACP_MODEL_DRIVER": "fake",
        "ACP_TRANSLATE_DRIVER": "fake",  # CR-025: deterministic MT, no download
        "ACP_STORAGE_DRIVER": "local",  # real local driver, exercised in tests
        "ACP_DATA_DIR": str(data_dir),
        "ACP_DATABASE_URL": f"sqlite:///{data_dir}/acp.db",
        "ACP_TRANSCRIPT_RETENTION_DAYS": "30",
        "ACP_MAX_UPLOAD_BYTES": str(500 * 1024 * 1024),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Clear every cached singleton so the new env takes effect.
    _reset_caches()
    yield
    _reset_caches()


def _reset_caches() -> None:
    from app.config import reset_settings_cache
    from app.jobs.store import reset_job_store_cache
    from app.model.registry import reset_transcriber_cache
    from app.storage.registry import reset_storage_cache
    from app.templates_store import reset_template_store_cache
    from app.translate.registry import reset_translator_cache

    reset_settings_cache()
    reset_job_store_cache()
    reset_transcriber_cache()
    reset_storage_cache()
    reset_template_store_cache()  # CR-022
    reset_translator_cache()  # CR-025


@pytest.fixture
def client():
    """A TestClient over a freshly-built app bound to the isolated env."""

    from fastapi.testclient import TestClient

    # Rebuild the app module so create_app() reads the patched env.
    import app.main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.app)


@pytest.fixture
def sample_audio_bytes() -> bytes:
    """A minimal WAV header + silence — sniffs as audio, no real decode needed."""

    # RIFF....WAVE + a tiny fmt/data so validate_upload sees 'audio'.
    riff = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
    fmt = b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00" \
        + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little") \
        + b"\x02\x00\x10\x00"
    data = b"data" + (0).to_bytes(4, "little")
    return riff + fmt + data
