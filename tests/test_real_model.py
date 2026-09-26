"""M1.3 / M2.4 — the gated real-model vertical slice.

Marked ``real_model`` and DESELECTED from the default GPU-free suite (see
pytest.ini). Run explicitly with weights available:

    ACP_MODEL_DRIVER=faster_whisper pytest -m real_model

Needs a real audio fixture at tests/fixtures/hello.wav (a short English clip
saying e.g. "hello world") and the faster-whisper package installed. These
assertions are the M2.4 milestone gate proven against the real model.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_model

FIXTURE = Path(__file__).parent / "fixtures" / "hello.wav"


@pytest.fixture
def real_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ACP_ENV", "development")
    monkeypatch.setenv("ACP_MODEL_DRIVER", "faster_whisper")
    monkeypatch.setenv("ACP_MODEL_TIER", os.environ.get("ACP_MODEL_TIER", "base"))
    monkeypatch.setenv("ACP_DEVICE", os.environ.get("ACP_DEVICE", "cpu"))
    monkeypatch.setenv("ACP_DATA_DIR", str(tmp_path / "data"))

    from app.config import reset_settings_cache
    from app.jobs.store import reset_job_store_cache
    from app.model.registry import reset_transcriber_cache
    from app.storage.registry import reset_storage_cache

    reset_settings_cache()
    reset_job_store_cache()
    reset_transcriber_cache()
    reset_storage_cache()

    import importlib

    import app.main as main_module

    importlib.reload(main_module)
    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


@pytest.mark.skipif(not FIXTURE.exists(), reason="no real audio fixture present")
def test_english_clip_transcribes_to_expected_words(real_client):
    # M2.4 step 1–3: submit → poll → transcript contains the expected words.
    with FIXTURE.open("rb") as fh:
        sub = real_client.post(
            "/transcribe",
            files={"file": ("hello.wav", fh, "audio/wav")},
            data={},
        ).json()
    body = real_client.get(f"/jobs/{sub['id']}").json()
    assert body["status"] == "completed"
    assert "hello" in body["result"]["text"].lower()
    assert body["detected_language"]  # a real detected language


@pytest.mark.skipif(not FIXTURE.exists(), reason="no real audio fixture present")
def test_corrupt_input_is_failed_not_crash(real_client):
    sub = real_client.post(
        "/transcribe",
        files={"file": ("bad.wav", b"RIFF\x00\x00\x00\x00WAVEnotreallyaudio", "audio/wav")},
        data={},
    )
    # Either rejected up front (4xx) or accepted then failed — never a 500.
    if sub.status_code == 202:
        body = real_client.get(f"/jobs/{sub.json()['id']}").json()
        assert body["status"] == "failed"
    else:
        assert 400 <= sub.status_code < 500
    assert real_client.get("/health").status_code == 200


# CR-011: PyAV in-process decode fallback (used when the ffmpeg binary is absent).
try:
    import av as _av  # noqa: F401

    _HAS_AV = True
except ImportError:
    _HAS_AV = False


@pytest.mark.skipif(not _HAS_AV, reason="PyAV (av) not installed")
def test_pyav_decode_fallback_when_ffmpeg_binary_absent(monkeypatch):
    """CR-011: with the ffmpeg binary forced absent, _decode_to_wav still produces a
    readable 16 kHz mono WAV via PyAV — the reason real voice→text works without a
    system ffmpeg. Synthesises a tiny WAV with PyAV, round-trips its bytes."""

    import io
    import shutil
    import struct
    import wave

    from app.model.drivers import faster_whisper_driver as fwd

    # Build a tiny valid WAV in memory (0.1 s of silence, 16 kHz mono) with stdlib —
    # a real container PyAV can open, without fiddling with AudioFrame planes.
    src = io.BytesIO()
    with wave.open(src, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<1600h", *([0] * 1600)))
    audio_bytes = src.getvalue()

    # Force the ffmpeg-binary path off so the PyAV fallback is exercised.
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    wav_path = fwd._decode_to_wav(audio_bytes)
    try:
        with wave.open(wav_path, "rb") as w:
            assert w.getframerate() == 16000
            assert w.getnchannels() == 1
    finally:
        import os

        if os.path.exists(wav_path):
            os.unlink(wav_path)


# CR-025 / D25: the real Argos translator. Gated like the model — skips cleanly
# unless argostranslate AND the en→hi package are installed (the pair the demo
# ships; install with argostranslate's package API, see .env.example).
try:
    import argostranslate.translate as _at  # noqa: F401

    _HAS_ARGOS = True
except ImportError:
    _HAS_ARGOS = False


def _argos_has_pair(src: str, tgt: str) -> bool:
    if not _HAS_ARGOS:
        return False
    try:
        import argostranslate.translate as at

        langs = {lang.code for lang in at.get_installed_languages()}
        return src in langs and tgt in langs
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    not _argos_has_pair("en", "hi"),
    reason="argostranslate en→hi package not installed",
)
def test_argos_real_translation_en_to_hi():
    """Real Argos: English → Hindi returns non-empty, non-Latin (Devanagari) text via
    the seam, with segment timing preserved (D18 grain unchanged)."""

    from app.model.contract import Segment
    from app.translate.drivers.argos_driver import ArgosTranslator

    segs = [Segment(0.0, 1.5, "hello world"), Segment(1.5, 3.0, "how are you")]
    out = ArgosTranslator().translate(
        "hello world how are you", segs, source="en", target="hi"
    )
    assert out.text and out.text.lower() != "hello world how are you"
    # Hindi is written in Devanagari (U+0900–U+097F) — at least one such char proves
    # a real translation happened, not an echo.
    assert any("ऀ" <= ch <= "ॿ" for ch in out.text)
    assert out.target == "hi"
    assert out.segments[0].start == 0.0 and out.segments[1].end == 3.0  # timing kept


@pytest.mark.skipif(
    not (_argos_has_pair("en", "bn") and _argos_has_pair("bn", "en")),
    reason="argostranslate en↔bn packages not installed",
)
def test_argos_real_bengali_round_trip():
    """Real Argos both directions (en→bn→en). Exercises the CR-025 MiniSBD fix: the
    bn→en package declares a stanza sentencizer, but stanza 1.10 has no Bengali SBD
    package — the driver forces MiniSBD so bn→en doesn't KeyError. A stanza-driven
    driver would raise here instead of returning English."""

    from app.model.contract import Segment
    from app.translate.drivers.argos_driver import ArgosTranslator

    tr = ArgosTranslator()
    to_bn = tr.translate("how are you", [Segment(0.0, 1.0, "how are you")],
                         source="en", target="bn")
    # Bengali is written in the Bengali block (U+0980–U+09FF).
    assert any("ঀ" <= ch <= "৿" for ch in to_bn.text)
    assert to_bn.target == "bn"

    back = tr.translate(to_bn.text, to_bn.segments, source="bn", target="en")
    # Round-tripped back to Latin English (the MiniSBD path worked — no stanza crash).
    assert back.text and any("a" <= ch.lower() <= "z" for ch in back.text)
    assert back.target == "en"


@pytest.mark.skipif(
    not (_argos_has_pair("bn", "en") and _argos_has_pair("en", "hi")),
    reason="argostranslate bn→en + en→hi pivot legs not installed",
)
def test_argos_real_pivot_bn_to_hi():
    """Real Argos: bn→hi with **no direct bn→hi package** — Argos pivots through English
    (bn→en→hi). Proves cross-pair translation works from the two legs alone. Also
    exercises the MiniSBD fix (the bn source leg declares a stanza sentencizer)."""

    from app.model.contract import Segment
    from app.translate.drivers.argos_driver import ArgosTranslator

    # "আমি ভালো আছি" = "I am fine" (Bengali) → expect Hindi (Devanagari).
    out = ArgosTranslator().translate(
        "আমি ভালো আছি", [Segment(0.0, 1.0, "আমি ভালো আছি")], source="bn", target="hi"
    )
    assert out.target == "hi"
    # Output is Devanagari (Hindi, U+0900–U+097F), NOT the Bengali block (U+0980–U+09FF):
    # if the pivot silently no-op'd, the text would still be Bengali.
    assert any("ऀ" <= ch <= "ॿ" for ch in out.text)
    assert not any("ঀ" <= ch <= "৿" for ch in out.text)
    assert out.segments[0].start == 0.0  # timing preserved through the pivot


@pytest.mark.skipif(
    not _argos_has_pair("en", "bn"),
    reason="argostranslate en→bn package not installed",
)
def test_argos_unroutable_source_falls_back_to_english():
    """Real Argos regression: an unroutable ``source`` (an unknown 3-letter code, or a
    valid ISO code with no installed package) must NOT crash with
    ``'NoneType' object has no attribute 'get_translation'`` — the driver maps it to
    English and still translates. Covers the exact runtime error reported for en→bn."""

    from app.model.contract import Segment
    from app.translate.drivers.argos_driver import ArgosTranslator

    tr = ArgosTranslator()
    for bad_source in ("eng", "zh", "xyz"):
        out = tr.translate(
            "how are you", [Segment(0.0, 1.0, "how are you")],
            source=bad_source, target="bn",
        )
        assert out.target == "bn"
        assert any("ঀ" <= ch <= "৿" for ch in out.text), bad_source  # real Bengali


@pytest.mark.skipif(not _HAS_ARGOS, reason="argostranslate not installed")
def test_argos_missing_pair_is_typed_error():
    """A pair with no installed package surfaces the typed TranslationError, not a 500."""

    from app.model.contract import Segment
    from app.translate.contract import TranslationError
    from app.translate.drivers.argos_driver import ArgosTranslator

    try:
        ArgosTranslator().translate(
            "hello", [Segment(0.0, 1.0, "hello")], source="en", target="zz"
        )
    except TranslationError:
        pass  # expected — the intended failure-is-a-state path
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"expected TranslationError, got {type(exc).__name__}") from exc
