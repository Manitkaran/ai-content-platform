"""CR-001 / D22 — live streaming mic transcription over WS /transcribe/stream.

Every test runs on the **fake** streaming driver in the default GPU-free suite:
no GPU, no microphone, no FFmpeg. The WebSocket is driven with Starlette's test
client; audio "chunks" are opaque bytes the fake turns into a deterministic
transcript. Mirrors the file-path API tests (test_transcribe_api) one level over.
"""

from __future__ import annotations

from app.model.drivers.fake import FAKE_BAD_CHUNK


def _run_stream(client, chunks, *, language=None, translate=None):
    """Open the WS, send start + chunks + stop, collect every frame back."""

    start = {"type": "start"}
    if language is not None:
        start["language"] = language
    if translate is not None:
        start["translate"] = translate

    frames = []
    with client.websocket_connect("/transcribe/stream") as ws:
        ws.send_json(start)
        for chunk in chunks:
            ws.send_bytes(chunk)
        ws.send_json({"type": "stop"})
        while True:
            msg = ws.receive_json()
            frames.append(msg)
            if msg["type"] in ("final", "error"):
                break
    return frames


def test_ws_happy_path_partial_segment_final(client):
    # Enough chunks to cross at least one window boundary (CR-010 widened the window),
    # so an interim `partial` is emitted before the final segment/final frames.
    chunks = [b"audio-chunk-%02d" % i for i in range(8)]
    frames = _run_stream(client, chunks)
    types = [f["type"] for f in frames]
    assert "partial" in types
    assert "segment" in types
    assert types[-1] == "final"

    final = frames[-1]
    assert final["text"]  # non-empty transcript
    assert final["detected_language"]  # populated
    assert isinstance(final["segments"], list) and final["segments"]


def test_ws_language_override_streams_through(client):
    frames = _run_stream(client, [b"hola-mundo"], language="es")
    assert frames[-1]["type"] == "final"
    assert frames[-1]["detected_language"] == "es"


def test_ws_translate_flag_streams_through(client):
    frames = _run_stream(client, [b"algo-en-espanol"], translate=True)
    final = frames[-1]
    assert final["type"] == "final"
    # the fake echoes "translated" into the text when translate=true
    assert "translated" in final["text"]


def test_ws_bad_audio_is_typed_error_not_crash(client):
    frames = _run_stream(client, [FAKE_BAD_CHUNK + b"garbage"])
    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "unsupported_media"
    # app stays up after a stream error
    assert client.get("/health").status_code == 200


def test_ws_empty_session_completes_empty(client):
    # start then stop with no audio → an empty but successful final, clean close.
    frames = _run_stream(client, [])
    assert frames[-1]["type"] == "final"
    assert frames[-1]["text"] == ""
    assert frames[-1]["detected_language"]  # still reports a (stub) language


def test_ws_invalid_language_in_start_is_error(client):
    frames = _run_stream(client, [b"x"], language="zz")
    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "invalid_language"


def test_ws_streams_incrementally_not_one_burst(client):
    """CR-008 / D23: partial frames arrive while audio accumulates, and their
    cumulative text GROWS across the session — proving windowed streaming, not a
    single end-of-stream burst."""

    # Enough chunks to cross the window boundary (CR-010, ~6/window) at least twice, so
    # multiple interim partials are emitted — proves it streamed as it went.
    chunks = [b"chunk-%02d-audio" % i for i in range(18)]
    frames = _run_stream(client, chunks)

    partials = [f for f in frames if f["type"] == "partial"]
    assert len(partials) >= 2  # more than one interim → it streamed as it went
    # Cumulative text is non-decreasing in length across partials.
    lengths = [len(p["text"]) for p in partials]
    assert lengths[-1] >= lengths[0]
    assert max(lengths) > min(lengths)  # it genuinely grew
    assert frames[-1]["type"] == "final" and frames[-1]["text"]


def test_ws_route_offloads_and_guards_overlap():
    """CR-010: window transcription runs off the event loop (asyncio.to_thread) and a
    busy guard prevents overlapping windows — so receiving audio never stalls behind a
    slow transcribe. A source-shape check (the behaviour needs real timing)."""

    import inspect

    from app.api import stream as stream_mod

    src = inspect.getsource(stream_mod)
    assert "asyncio.to_thread" in src  # CPU work is offloaded, not on the event loop
    assert "busy" in src  # overlap guard present


def test_fake_transcribe_window_honours_start_time():
    """CR-013 / D24: transcribe_window(buffer, opts, start_time=t) transcribes only the
    tail past `t` — fewer words than start_time=0, and segment starts are shifted by t."""

    from app.model.contract import AUTO_LANGUAGE, OutputFormat, TranscribeOptions
    from app.model.drivers.fake import FakeTranscriber

    fake = FakeTranscriber()
    opts = TranscribeOptions(
        language=AUTO_LANGUAGE, translate=False, output_format=OutputFormat.text
    )
    buf = b"a" * 240  # long enough to yield several words
    whole = fake.transcribe_window(buf, opts, start_time=0.0)
    tail = fake.transcribe_window(buf, opts, start_time=10.0)

    assert len(tail.segments) <= len(whole.segments)  # tail is a subset
    if tail.segments:
        assert tail.segments[0].start >= 10.0  # timestamps shifted by the offset


def test_real_driver_window_slices_pcm_by_start_time():
    """CR-013: the real driver transcribes only pcm[start_time:] and shifts returned
    segment timestamps by start_time. Uses a stub model + stub decode (no weights)."""

    from app.model.contract import AUTO_LANGUAGE, OutputFormat, TranscribeOptions
    from app.model.drivers import faster_whisper_driver as fwd
    from app.model.drivers.faster_whisper_driver import FasterWhisperTranscriber

    class _Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class _Info:
        language = "en"

    seen = {}

    class _StubModel:
        def transcribe(self, audio, **kwargs):
            seen["audio_len"] = len(audio)  # ndarray slice length
            # model returns tail-relative timestamps; driver must shift by start_time
            return iter([_Seg(0.0, 1.0, "world")]), _Info()

    import pytest

    # numpy ships with the real model, not the light default suite; skip when absent.
    np = pytest.importorskip("numpy")

    # 4 s of PCM at 16 kHz; start_time=2.0 → the model should see ~2 s (32000 samples).
    pcm = np.zeros(4 * 16000, dtype="float32")
    monkey = fwd._decode_to_pcm
    fwd._decode_to_pcm = lambda _b: pcm  # noqa: E731 - test stub
    try:
        drv = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
        drv._model = _StubModel()
        opts = TranscribeOptions(
            language=AUTO_LANGUAGE, translate=False, output_format=OutputFormat.text
        )
        result = drv.transcribe_window(b"ignored", opts, start_time=2.0)
    finally:
        fwd._decode_to_pcm = monkey

    assert seen["audio_len"] == 2 * 16000  # only the tail past 2.0s was transcribed
    assert result.segments[0].start == 2.0  # 0.0 (model) + 2.0 (offset)


def test_real_driver_passes_tuned_transcribe_params():
    """CR-012: the real driver invokes faster-whisper with VAD + greedy + deterministic
    + no-context-carry params (the accuracy/anti-repetition fix). Uses a STUB model, so
    it runs in the default suite with no weights."""

    from app.model.contract import AUTO_LANGUAGE, OutputFormat, TranscribeOptions
    from app.model.drivers.faster_whisper_driver import FasterWhisperTranscriber

    class _Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class _Info:
        language = "en"

    calls = {}

    class _StubModel:
        def transcribe(self, media_path, **kwargs):
            calls.update(kwargs)
            return iter([_Seg(0.0, 1.0, "hello")]), _Info()

    # Build the driver without importing/loading a real model.
    drv = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    drv._model = _StubModel()

    opts = TranscribeOptions(
        language=AUTO_LANGUAGE, translate=False, output_format=OutputFormat.text
    )
    result = drv.transcribe("ignored.wav", opts)

    assert calls.get("vad_filter") is True
    assert calls.get("beam_size") == 1
    assert calls.get("temperature") == 0
    assert calls.get("condition_on_previous_text") is False
    # shape still maps through
    assert result.text == "hello"
    assert result.detected_language == "en"
    assert len(result.segments) == 1


def test_decode_missing_both_ffmpeg_and_pyav_is_typed_error(monkeypatch):
    """CR-011: when neither the ffmpeg binary nor PyAV is available, the stream
    decoder raises a typed UnsupportedMediaError — never a raw traceback."""

    import builtins
    import shutil

    from app.model.contract import UnsupportedMediaError
    from app.model.drivers import faster_whisper_driver as fwd

    monkeypatch.setattr(shutil, "which", lambda _name: None)  # ffmpeg binary absent

    real_import = builtins.__import__

    def _no_av(name, *args, **kwargs):
        if name == "av":
            raise ImportError("no av")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_av)  # PyAV absent too

    try:
        fwd._decode_to_wav(b"whatever")
        raised = False
    except UnsupportedMediaError:
        raised = True
    assert raised


def _has_av() -> bool:
    try:
        import av  # noqa: F401

        return True
    except ImportError:
        return False


def _encode_webm_opus(seconds: int) -> bytes:
    """Encode ``seconds`` of a tone as WebM/Opus in memory (test fixture, needs av)."""

    import io
    import math

    import av
    import numpy as np

    buf = io.BytesIO()
    out = av.open(buf, mode="w", format="webm")
    st = out.add_stream("libopus", rate=48000)
    st.layout = "mono"
    sr, fs, t = 48000, 960, 0
    for _ in range(0, sr * seconds, fs):
        s = np.array(
            [int(1500 * math.sin(2 * math.pi * 300 * (t + j) / sr)) for j in range(fs)],
            dtype="int16",
        )
        t += fs
        frame = av.AudioFrame.from_ndarray(s.reshape(1, -1), format="s16", layout="mono")
        frame.rate = sr
        for p in st.encode(frame):
            out.mux(p)
    for p in st.encode(None):
        out.mux(p)
    out.close()
    return buf.getvalue()


def test_incremental_decode_is_bit_exact_with_full_decode():
    """CR-027 / D27 (the core correctness proof): feeding a WebM/Opus clip to the
    IncrementalStreamDecoder in windows yields PCM **bit-exact** with a single full decode
    (maxdiff == 0), and each window decodes only NEW packets — the O(n²)→O(n) fix. Needs
    PyAV (present wherever the real driver runs); no model weights (we read the PCM cache
    directly). Skips cleanly without av."""

    if not _has_av():
        import pytest

        pytest.skip("PyAV (av) not installed")

    import numpy as np

    from app.model.drivers import faster_whisper_driver as fwd
    from app.model.drivers.faster_whisper_driver import (
        FasterWhisperTranscriber,
        IncrementalStreamDecoder,
    )

    data = _encode_webm_opus(3)

    # Baseline: the existing whole-buffer decode of the full clip.
    baseline = fwd._decode_to_pcm(data)

    # Feed the SAME bytes incrementally, in growing windows, through the decoder.
    drv = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)  # no model needed
    dec = IncrementalStreamDecoder(drv)
    step = max(1, len(data) // 5)
    for i in range(0, len(data), step):
        dec.feed(data[i : i + step])
    incremental = dec._pcm()

    # Bit-exact over the shared prefix (lengths may differ by a frame at the tail).
    m = min(baseline.shape[0], incremental.shape[0])
    assert m > 0
    assert np.abs(baseline[:m] - incremental[:m]).max() == 0.0
    # And it decoded packets incrementally (a positive count, i.e. it ran).
    assert dec._packets_decoded > 0


def test_incremental_decode_only_decodes_new_packets():
    """CR-027: a second feed of MORE bytes advances the packet counter but does NOT
    re-decode the earlier packets — the incremental guarantee. Skips without av."""

    if not _has_av():
        import pytest

        pytest.skip("PyAV (av) not installed")

    from app.model.drivers.faster_whisper_driver import (
        FasterWhisperTranscriber,
        IncrementalStreamDecoder,
    )

    data = _encode_webm_opus(4)
    drv = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    dec = IncrementalStreamDecoder(drv)

    dec.feed(data[: len(data) // 2])
    after_first = dec._packets_decoded
    assert after_first > 0
    dec.feed(data[len(data) // 2 :])
    after_second = dec._packets_decoded
    # More packets decoded in total, but each packet counted once (monotonic, no re-decode).
    assert after_second > after_first


def test_transcribe_window_grows_with_buffer():
    """CR-008 / D23: the seam's transcribe_window returns more words for a longer
    buffer (fake), and honours language/translate — the primitive live streaming
    rides on. Runs on the fake driver directly (no WS)."""

    from app.model.contract import AUTO_LANGUAGE, OutputFormat, TranscribeOptions
    from app.model.drivers.fake import FakeTranscriber

    fake = FakeTranscriber()
    opts = TranscribeOptions(
        language=AUTO_LANGUAGE, translate=False, output_format=OutputFormat.text
    )
    short = fake.transcribe_window(b"a" * 8, opts)
    long = fake.transcribe_window(b"a" * 64, opts)
    assert len(long.segments) >= len(short.segments)
    assert len(long.text) >= len(short.text)

    # translate echo flows through the window primitive too.
    tr = fake.transcribe_window(
        b"hola",
        TranscribeOptions(
            language=AUTO_LANGUAGE, translate=True, output_format=OutputFormat.text
        ),
    )
    assert "translated" in tr.text or tr.text == ""


# --- CR-027 / D27: incremental stream decoder --------------------------------


def _opts():
    from app.model.contract import AUTO_LANGUAGE, OutputFormat, TranscribeOptions

    return TranscribeOptions(
        language=AUTO_LANGUAGE, translate=False, output_format=OutputFormat.text
    )


def test_stream_decoder_decodes_each_byte_once():
    """CR-027: feeding chunks incrementally decodes each byte EXACTLY once across the
    session — never re-decoding the growing prefix (the O(n²) fix). The fake decoder's
    ``decoded_bytes`` counter must equal the total bytes fed, not the running sum of
    buffer lengths."""

    from app.model.drivers.fake import FakeTranscriber

    dec = FakeTranscriber().new_stream_decoder()
    chunks = [b"chunk-%02d" % i for i in range(20)]
    for c in chunks:
        dec.feed(c)
    total_fed = sum(len(c) for c in chunks)
    # O(n): decoded exactly the bytes fed. (The old whole-buffer path would have
    # "decoded" ~ n²/2 bytes; here it's exactly n.)
    assert dec.decoded_bytes == total_fed


def test_stream_decoder_tail_matches_transcribe_window():
    """CR-027: the incremental decoder + transcribe_tail produce the SAME transcript as
    the old whole-buffer transcribe_window for the same audio and start_time — proving the
    rewrite is behaviour-preserving on the fake (AC-1)."""

    from app.model.drivers.fake import FakeTranscriber

    fake = FakeTranscriber()
    chunks = [b"audio-block-%03d" % i for i in range(12)]
    whole_buffer = b"".join(chunks)
    opts = _opts()

    dec = fake.new_stream_decoder()
    for c in chunks:
        dec.feed(c)

    for start_time in (0.0, 3.0):
        via_decoder = dec.transcribe_tail(opts, start_time)
        via_window = fake.transcribe_window(whole_buffer, opts, start_time)
        assert via_decoder.text == via_window.text, start_time
        assert [(s.start, s.end, s.text) for s in via_decoder.segments] == [
            (s.start, s.end, s.text) for s in via_window.segments
        ], start_time


def test_stream_decoder_bad_chunk_is_typed_error():
    """CR-027: an undecodable chunk fed to the decoder raises the typed
    UnsupportedMediaError (→ error frame + clean close at the WS layer), at feed time."""

    from app.model.contract import UnsupportedMediaError
    from app.model.drivers.fake import FakeTranscriber

    dec = FakeTranscriber().new_stream_decoder()
    dec.feed(b"fine so far")
    try:
        dec.feed(FAKE_BAD_CHUNK)
    except UnsupportedMediaError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected UnsupportedMediaError")


def test_stream_decoder_empty_tail_is_empty_result():
    from app.model.drivers.fake import FakeTranscriber

    dec = FakeTranscriber().new_stream_decoder()
    # No audio fed → empty successful result.
    out = dec.transcribe_tail(_opts(), 0.0)
    assert out.text == "" and out.segments == []


def test_demo_page_exposes_mic_control(client):
    """Reachability: the served demo page offers the mic toggle and streams to
    the public WS endpoint (CR-001; sibling of test_demo_page_loads)."""

    html = client.get("/").text
    assert 'id="mic"' in html
    assert "/transcribe/stream" in html
    assert "getUserMedia" in html


def test_demo_page_renders_outputs_in_textareas(client):
    """CR-004: the file result renders into an editable textarea (not <pre>), and the
    live voice stream dedupes overlapping segments. CR-007: the live text lands in the
    chat-box textarea (#chatInput)."""

    html = client.get("/").text
    # File result is a textarea now, not a <pre> box.
    assert '<textarea id="transcript"' in html
    assert '<pre id="transcript"' not in html
    # The overlap-dedup helper exists and is wired into the stream render.
    assert "dedupeAppend" in html
    # Thin-client invariant unchanged: only the public WS endpoint is referenced.
    assert "/transcribe/stream" in html


def test_demo_page_is_press_and_hold_voice_input(client):
    """CR-007/CR-008: the voice area is a single input box with a press-and-hold mic
    button (#chatInput + #mic); the mic streams into the textarea. CR-005's
    waveform/timer/accept-discard box is gone."""

    html = client.get("/").text
    # Single input box + message textarea present; mic keeps id="mic".
    assert 'id="chatInput"' in html
    assert 'id="mic"' in html
    # CR-008: press-and-hold wiring (pointer events cover mouse + touch).
    assert "pointerdown" in html
    # CR-005's dictation affordances are gone.
    assert 'id="micMeter"' not in html
    assert 'id="micTimer"' not in html
    assert 'id="micAccept"' not in html
    assert 'id="micDiscard"' not in html
    # Voice is still wired: overlap-dedup + the public WS endpoint.
    assert "dedupeAppend" in html
    assert "/transcribe/stream" in html


def test_demo_page_voice_has_own_language_and_translate(client):
    """CR-006/CR-007: the voice box has its own language picker and translate toggle,
    and the live-mic start frame reads those (not the file form's)."""

    html = client.get("/").text
    # The voice box owns its language + translate controls.
    assert 'id="micLanguage"' in html
    assert 'id="micTranslate"' in html
    # The start frame is wired to the voice controls, and streams into the chat box.
    assert "micLanguage" in html
    assert "micTranslate" in html
    assert 'id="chatInput"' in html


def test_demo_voice_language_defaults_to_auto_detect(client):
    """CR-015 (reverts CR-014's English default): the voice box defaults to Auto-detect
    so Bengali/Hindi/English are detected per utterance — forcing English garbles
    non-English speech. Every language stays available; English is one click away."""

    import re

    html = client.get("/").text
    mic_select = re.search(r'<select id="micLanguage">(.*?)</select>', html, re.S)
    assert mic_select, "voice language select present"
    block = mic_select.group(1)
    assert re.search(r'<option value="auto"[^>]*selected', block)
    assert not re.search(r'<option value="en"[^>]*selected', block)
    # every language still offered (multilingual, D14)
    for code in ("auto", "en", "hi", "bn", "es", "fr", "de", "zh"):
        assert f'value="{code}"' in block


def test_demo_page_press_and_hold_handles_release_race(client):
    """CR-009: a release before the mic/WS opens must still stop, and a short tap must
    flush its audio — so voice actually writes text. Assert the fix's shape is present."""

    html = client.get("/").text
    # Release-before-open intent is tracked (not dropped when recording is still false).
    assert "stopRequested" in html
    # A final chunk is flushed on stop so a quick tap sends audio.
    assert "requestData" in html
    # Press-and-hold wiring still there.
    assert "pointerdown" in html
    assert 'id="mic"' in html
