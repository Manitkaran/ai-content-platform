"""M1.1 / M1.2 — the contract, the registry, and the fake driver."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.model.contract import (
    TranscribeOptions,
    Transcriber,
    TranscriptResult,
    UnsupportedMediaError,
)
from app.model.drivers.fake import FAKE_MARKER, FakeTranscriber
from app.model.registry import build_transcriber


def _write(tmp_path, name, data=b"x"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_registry_resolves_fake_from_config():
    driver = build_transcriber(Settings(env={"ACP_MODEL_DRIVER": "fake"}))
    assert isinstance(driver, FakeTranscriber)
    assert isinstance(driver, Transcriber)  # satisfies the protocol


def test_fake_is_deterministic(tmp_path):
    fake = FakeTranscriber()
    path = _write(tmp_path, "clip.mp3")
    r1 = fake.transcribe(path, TranscribeOptions())
    r2 = fake.transcribe(path, TranscribeOptions())
    assert r1 == r2
    assert isinstance(r1, TranscriptResult)


def test_fake_changes_with_input(tmp_path):
    fake = FakeTranscriber()
    a = fake.transcribe(_write(tmp_path, "a.mp3"), TranscribeOptions())
    b = fake.transcribe(_write(tmp_path, "b.mp3"), TranscribeOptions())
    assert a.text != b.text


def test_fake_output_is_recognisably_synthetic(tmp_path):
    fake = FakeTranscriber()
    r = fake.transcribe(_write(tmp_path, "clip.mp3"), TranscribeOptions())
    assert FAKE_MARKER in r.text  # can never be mistaken for a real transcript


def test_fake_missing_file_is_typed_error():
    with pytest.raises(UnsupportedMediaError):
        FakeTranscriber().transcribe("/no/such/file.mp3", TranscribeOptions())


def test_fake_empty_stem_yields_empty_but_successful(tmp_path):
    fake = FakeTranscriber()
    r = fake.transcribe(_write(tmp_path, "silence.wav"), TranscribeOptions())
    assert r.text == ""  # empty result, not an error (M4.3)
