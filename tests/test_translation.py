"""CR-025 / D25 — arbitrary source→target translation (``target_language``).

Proves the MT seam threads through the file path, the batch path, subtitle export,
and the live-streaming path; that it is off by default (byte-identical to today);
that validation rejects unknown / ``auto`` targets; and that a seam failure is a
typed error (a ``failed`` job / a stream error frame), never a 500. All on the
deterministic ``fake`` translator — no model, no download.
"""

from __future__ import annotations

import json

from app.model.contract import Segment, TranscribeOptions
from app.translate.drivers.fake import FAKE_BAD_TARGET, FakeTranslator

# --- the fake Translator driver (the seam contract) --------------------------


def test_fake_translator_wraps_text_and_segments():
    tr = FakeTranslator()
    segs = [Segment(start=0.0, end=1.0, text="hello"), Segment(1.0, 2.0, "world")]
    out = tr.translate("hello world", segs, source="en", target="hi")
    assert out.text == "[MT en→hi] hello world"
    assert out.target == "hi"
    assert out.segments[0].text == "[MT en→hi] hello"
    # Timing is preserved — only text changes (D18 grain unchanged).
    assert out.segments[0].start == 0.0 and out.segments[1].end == 2.0


def test_fake_translator_empty_source_is_auto():
    out = FakeTranslator().translate("x", [], source="", target="bn")
    assert out.text == "[MT auto→bn] x"


def test_fake_translator_bad_target_raises_typed_error():
    from app.translate.contract import TranslationError

    try:
        FakeTranslator().translate("x", [], source="en", target=FAKE_BAD_TARGET)
    except TranslationError as exc:
        assert exc.code == "translation_error"
    else:  # pragma: no cover
        raise AssertionError("expected TranslationError")


# --- CR-025: Argos source-code resolution (the "'NoneType' … get_translation" fix) ---
# Argos crashes on a from-code it has no installed language for (an unknown 3-letter
# code like 'eng', or a valid ISO code with no package). _resolve_source maps any such
# source — and auto/empty — to English (the pivot hub). Gate-free: a stub `at` stands in
# for the argostranslate module, so this needs no argostranslate install.


def _fake_at(installed_codes):
    class _Lang:
        def __init__(self, code):
            self.code = code

    class _At:
        def get_installed_languages(self):
            return [_Lang(c) for c in installed_codes]

    return _At()


def test_resolve_source_maps_unroutable_to_english():
    from app.translate.drivers.argos_driver import _resolve_source

    at = _fake_at({"en", "bn", "hi"})
    # Known, installed source → honoured.
    assert _resolve_source(at, "bn", "hi") == "bn"
    # auto / empty → English.
    assert _resolve_source(at, "auto", "bn") == "en"
    assert _resolve_source(at, "", "bn") == "en"
    # Unknown 3-letter code (the crash trigger) → English.
    assert _resolve_source(at, "eng", "bn") == "en"
    # Valid ISO code but no installed package for it → English (the pivot hub).
    assert _resolve_source(at, "zh", "bn") == "en"


def test_resolve_source_falls_back_if_enumeration_fails():
    from app.translate.drivers.argos_driver import _resolve_source

    class _Broken:
        def get_installed_languages(self):
            raise RuntimeError("cannot enumerate")

    # If we can't list installed languages, don't guess — pass the source through as-is.
    assert _resolve_source(_Broken(), "bn", "hi") == "bn"
    # auto still short-circuits to English before enumeration is attempted.
    assert _resolve_source(_Broken(), "auto", "hi") == "en"


# --- file path: POST /transcribe --------------------------------------------


def _submit(client, audio, **data):
    return client.post(
        "/transcribe",
        files={"file": ("clip.wav", audio, "audio/wav")},
        data=data,
    )


def test_target_language_translates_text_and_segments(client, sample_audio_bytes):
    r = _submit(client, sample_audio_bytes, language="en", target_language="hi")
    assert r.status_code == 202
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert body["status"] == "completed"
    # Every segment (and the whole text) went through the en→hi MT stage.
    assert "[MT en→hi]" in body["result"]["text"]
    assert all("[MT en→hi]" in s["text"] for s in body["result"]["segments"])
    # The reported language is now the target.
    assert body["result"]["detected_language"] == "hi"


def test_no_target_language_is_unchanged(client, sample_audio_bytes):
    """AC-2: omitting target_language is byte-identical to today (no MT stage)."""

    r = _submit(client, sample_audio_bytes, language="en")
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert "[MT" not in body["result"]["text"]


def test_unknown_target_language_is_422(client, sample_audio_bytes):
    r = _submit(client, sample_audio_bytes, target_language="zz")
    assert r.status_code == 422


def test_auto_target_language_is_422(client, sample_audio_bytes):
    """AC-3: 'auto' is not a valid target — you must name where you're going."""

    r = _submit(client, sample_audio_bytes, target_language="auto")
    assert r.status_code == 422


def test_target_language_persists_on_the_job(client, sample_audio_bytes):
    r = _submit(client, sample_audio_bytes, target_language="bn")
    from app.jobs.store import get_job_store

    assert get_job_store().get(r.json()["id"]).options.target_language == "bn"


# --- batch path --------------------------------------------------------------


def test_batch_applies_target_language_to_every_file(client, sample_audio_bytes):
    files = [
        ("files", ("a.wav", sample_audio_bytes, "audio/wav")),
        ("files", ("b.wav", sample_audio_bytes, "audio/wav")),
    ]
    r = client.post("/transcribe/batch", files=files, data={"target_language": "hi"})
    assert r.status_code == 200
    for row in r.json()["results"]:
        body = client.get(f"/jobs/{row['id']}").json()
        assert "[MT" in body["result"]["text"]
        assert body["result"]["detected_language"] == "hi"


# --- subtitle export renders the translated text -----------------------------


def test_subtitle_export_of_translated_job(client, sample_audio_bytes):
    """AC-5: an SRT export of a translated job carries the translated text."""

    r = _submit(
        client, sample_audio_bytes, language="en", target_language="hi", output_format="srt"
    )
    sub = client.get(f"/jobs/{r.json()['id']}/subtitle?format=srt")
    assert sub.status_code == 200
    assert "[MT en→hi]" in sub.text


# --- streaming path: WS /transcribe/stream -----------------------------------


def _drive_stream(client, start_frame, chunks):
    frames = []
    with client.websocket_connect("/transcribe/stream") as ws:
        ws.send_text(json.dumps(start_frame))
        for c in chunks:
            ws.send_bytes(c)
        ws.send_text(json.dumps({"type": "stop"}))
        while True:
            msg = json.loads(ws.receive_text())
            frames.append(msg)
            if msg["type"] in ("final", "error"):
                break
    return frames


def test_streaming_translates_partials_and_final(client):
    audio = b"\x11\x22\x33\x44" * 40  # enough bytes for several fake words
    frames = _drive_stream(
        client, {"type": "start", "language": "en", "target_language": "hi"}, [audio]
    )
    final = next(f for f in frames if f["type"] == "final")
    assert "[MT en→hi]" in final["text"]
    # partials, when emitted, are translated too.
    partials = [f for f in frames if f["type"] == "partial"]
    assert all("[MT en→hi]" in p["text"] for p in partials)


def test_streaming_without_target_is_unchanged(client):
    audio = b"\x11\x22\x33\x44" * 40
    frames = _drive_stream(client, {"type": "start", "language": "en"}, [audio])
    final = next(f for f in frames if f["type"] == "final")
    assert "[MT" not in final["text"]


def test_streaming_auto_target_is_protocol_error(client):
    frames = _drive_stream(
        client, {"type": "start", "target_language": "auto"}, [b"\x00\x00"]
    )
    assert frames[0]["type"] == "error"
    assert frames[0]["code"] == "invalid_language"


# --- service-level: a TranslationError becomes a failed job ------------------


def test_translation_failure_is_a_failed_job(client, sample_audio_bytes, monkeypatch):
    """AC-7 (fake analogue): a seam failure → failed job, never a 500."""

    from app.translate.contract import TranslationError

    def boom(self, text, segments, *, source, target):
        raise TranslationError("no package for this pair")

    monkeypatch.setattr(FakeTranslator, "translate", boom)
    r = _submit(client, sample_audio_bytes, target_language="hi")
    assert r.status_code == 202
    body = client.get(f"/jobs/{r.json()['id']}").json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "translation_error"


def test_options_default_target_is_empty():
    assert TranscribeOptions().target_language == ""


# --- demo page carries the "Translate to" pickers (file + voice) -------------


def test_demo_page_has_translate_to_pickers(client):
    """CR-025: both the file card and the voice box expose a target picker, and the
    file one serializes (has a name) so the thin client posts target_language."""

    html = client.get("/").text
    assert 'id="target_language"' in html
    assert 'name="target_language"' in html  # file form serializes it
    assert 'id="micTargetLanguage"' in html  # voice box's own picker
    # Still a thin client of the public API (D6) — no private surface.
    assert "/internal" not in html
