"""The real ``faster-whisper`` transcriber (M1.3).

This is the ONLY module that imports ``faster_whisper``. It loads the configured
model tier once (device ``cpu`` by default, ``cuda`` opt-in — D13), transcribes
media, and maps the library's segments into our DTOs. Library types never cross
the seam boundary.

The import of ``faster_whisper`` is lazy (inside ``__init__``) so this file can
be imported for type/registry purposes without the heavy dependency present; the
GPU-free default suite never constructs this driver.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable, Iterator

from app.model.contract import (
    AUTO_LANGUAGE,
    Segment,
    StreamEvent,
    StreamEventType,
    TranscribeOptions,
    TranscriptResult,
    UnsupportedMediaError,
)


class FasterWhisperTranscriber:
    """Wraps ``faster_whisper.WhisperModel`` behind the ``Transcriber`` contract."""

    def __init__(self, tier: str = "base", device: str = "cpu") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "faster-whisper is not installed. Install it (pip install "
                "faster-whisper) or run with ACP_MODEL_DRIVER=fake."
            ) from exc

        # int8 keeps CPU inference tractable; float16 is a good default on GPU.
        compute_type = "float16" if device == "cuda" else "int8"
        self._model = WhisperModel(tier, device=device, compute_type=compute_type)

    def transcribe(
        self, media_path: str, options: TranscribeOptions
    ) -> TranscriptResult:
        # The file path hands the model the path directly (it decodes it); the streaming
        # path (CR-013) hands a PCM array. Both share the tuned model call below.
        return self._run_model(media_path, options, offset=0.0)

    def _transcribe_pcm(self, audio, options, offset):  # noqa: ANN001
        """Transcribe a float32 16 kHz mono PCM ndarray (CR-013), shifting returned
        segment timestamps by ``offset`` seconds back to absolute time."""

        return self._run_model(audio, options, offset=offset)

    def _run_model(self, audio, options, offset):  # noqa: ANN001
        """The one tuned model call (CR-012). ``audio`` is a file path or a PCM ndarray
        (faster-whisper accepts either). ``offset`` shifts segment timestamps (CR-013)."""

        task = "translate" if options.translate else "transcribe"
        language = None if options.language == AUTO_LANGUAGE else options.language

        try:
            # CR-012: CPU/streaming-tuned decoding. VAD skips silence (fewer
            # hallucinations in gaps, less audio to decode); greedy (beam_size=1) is
            # faster on CPU; temperature=0 is deterministic so re-transcribed partials
            # don't flip; condition_on_previous_text=False is the anti-repetition fix
            # — a partial buffer must not seed a loop from its own earlier guess.
            segments_iter, info = self._model.transcribe(
                audio,
                task=task,
                language=language,
                # CR-022: bias decoding toward the caller's vocabulary (names/jargon).
                # Empty prompt → None so behaviour is identical to no prompt.
                initial_prompt=options.prompt or None,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                beam_size=1,
                temperature=0,
                condition_on_previous_text=False,
            )
            segments = [
                Segment(
                    start=float(s.start) + offset,  # CR-013: tail-relative → absolute
                    end=float(s.end) + offset,
                    text=s.text.strip(),
                )
                for s in segments_iter
            ]
        except Exception as exc:  # noqa: BLE001 - map any library failure to typed
            raise UnsupportedMediaError(
                f"faster-whisper could not process the media: {exc}"
            ) from exc

        detected = getattr(info, "language", None) or options.language
        # When translating to English, the effective output language is English.
        if options.translate:
            detected_out = "en"
        else:
            detected_out = detected
        text = " ".join(seg.text for seg in segments).strip()
        return TranscriptResult(
            text=text, detected_language=detected_out, segments=segments
        )

    def transcribe_window(
        self, buffer: bytes, options: TranscribeOptions, start_time: float = 0.0
    ) -> TranscriptResult:
        """Transcribe the accumulated buffer from ``start_time`` on (CR-008 / D23;
        CR-013 / D24 added ``start_time``).

        Decodes the whole buffer to a 16 kHz mono PCM array (cheap; WebM/Opus can't be
        sliced mid-stream), then runs the model **only on the tail** ``pcm[start_time:]``
        — the D24 fix that stops re-transcribing already-committed audio every window, so
        per-update cost is bounded to new audio instead of growing O(n²). Returned
        segment timestamps are shifted back to absolute time. ``start_time=0.0`` is the
        whole-buffer D23 behaviour. Empty/short tail → empty result.
        """

        detected = "en" if options.translate else options.language
        if not buffer:
            return TranscriptResult(text="", detected_language=detected, segments=[])

        pcm = _decode_to_pcm(buffer)
        start_sample = max(0, int(start_time * 16000))
        tail = pcm[start_sample:]
        if tail.shape[0] == 0:
            return TranscriptResult(text="", detected_language=detected, segments=[])
        return self._transcribe_pcm(tail, options, offset=start_time)

    def transcribe_stream(
        self, chunks: Iterable[bytes], options: TranscribeOptions
    ) -> Iterator[StreamEvent]:
        """Windowed live transcription over buffered audio (CR-001 / D22).

        The browser streams container audio (WebM/Opus). We buffer it, decode to
        the 16 kHz mono WAV the model expects with FFmpeg (the same tool M2.5
        uses), transcribe, and emit a ``partial`` + finalized ``segment`` per
        Whisper segment, then one ``final``. An undecodable stream surfaces as a
        typed :class:`UnsupportedMediaError`; the WS layer turns it into an error
        frame and a clean close. No audio → an empty successful ``final``.
        """

        buffer = bytearray()
        for chunk in chunks:
            buffer.extend(chunk)

        detected_lang = "en" if options.translate else options.language
        if not buffer:
            yield StreamEvent(
                type=StreamEventType.final,
                result=TranscriptResult(
                    text="", detected_language=detected_lang, segments=[]
                ),
            )
            return

        wav_path = _decode_to_wav(bytes(buffer))
        try:
            result = self.transcribe(wav_path, options)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

        for seg in result.segments:
            yield StreamEvent(
                type=StreamEventType.partial,
                text=seg.text,
                detected_language=result.detected_language,
            )
            yield StreamEvent(type=StreamEventType.segment, segment=seg)
        yield StreamEvent(type=StreamEventType.final, result=result)

    def new_stream_decoder(self) -> IncrementalStreamDecoder:
        """The stateful per-session decoder (CR-027 / D27). Holds a persistent codec so
        incremental decode is bit-exact with a full decode; transcribes the cached PCM's
        tail with this driver's tuned model call — no re-decode per window."""

        return IncrementalStreamDecoder(self)


class IncrementalStreamDecoder:
    """Stateful per-session decoder (CR-027 / D27): decode only new bytes, model the tail.

    The O(n²) fix. ``feed(new_bytes)`` decodes **only the newly appended container bytes**
    into a persistent PCM ndarray; ``transcribe_tail`` runs the model on ``pcm[start_time:]``
    of that cache with **no decode**. Correctness rides on a **persistent Opus codec**:
    each ``feed`` re-parses (demux) the growing buffer — cheap — but calls ``.decode()`` only
    on packets past those already decoded, through the same long-lived ``CodecContext`` so
    Opus inter-packet state is continuous. That is what makes the incremental PCM bit-exact
    with a single full decode (verified ``maxdiff == 0``); decoding isolated packets with a
    fresh codec would corrupt samples at packet boundaries. The whole thing lives inside
    ``drivers/`` so ``av`` never crosses the seam.
    """

    def __init__(self, transcriber: FasterWhisperTranscriber) -> None:
        self._transcriber = transcriber
        self._buffer = bytearray()  # all container bytes fed (needed to re-demux)
        self._decoder = None  # persistent av.CodecContext, created on first audio packet
        self._resampler = None
        self._packets_decoded = 0  # how many packets we've already run through the codec
        self._pcm_chunks: list = []  # decoded 16 kHz mono int16 ndarrays, in order

    def feed(self, new_bytes: bytes) -> None:
        """Decode only the newly appended bytes into the persistent PCM cache (CR-027)."""

        if not new_bytes:
            return
        self._buffer.extend(new_bytes)
        try:
            self._decode_new_packets()
        except UnsupportedMediaError:
            raise
        except Exception as exc:  # noqa: BLE001 - any decode failure → typed, clean close
            raise UnsupportedMediaError(
                f"could not decode the live audio stream: {str(exc)[:500]}"
            ) from exc

    def _decode_new_packets(self) -> None:
        import io

        import av

        container = av.open(io.BytesIO(bytes(self._buffer)))
        try:
            stream = container.streams.audio[0]
            if self._decoder is None:
                from av.codec import CodecContext

                self._decoder = CodecContext.create(
                    stream.codec_context.name, "r"
                )
                self._decoder.extradata = stream.codec_context.extradata
                self._resampler = av.audio.resampler.AudioResampler(
                    format="s16", layout="mono", rate=16000
                )
            idx = 0
            for packet in container.demux(stream):
                if packet.dts is None:  # trailing flush packet from demux
                    continue
                if idx < self._packets_decoded:
                    idx += 1
                    continue  # already decoded in an earlier feed — never re-decode
                # Detach the packet from this throwaway container so the persistent
                # decoder owns it; decode through the SAME codec so Opus state carries.
                detached = av.Packet(bytes(packet))
                for frame in self._decoder.decode(detached):
                    for rframe in self._resampler.resample(frame):
                        self._pcm_chunks.append(rframe.to_ndarray().reshape(-1))
                idx += 1
            self._packets_decoded = idx
        finally:
            container.close()

    def _pcm(self):  # noqa: ANN202 - returns np.ndarray float32
        import numpy as np

        if not self._pcm_chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(self._pcm_chunks).astype("float32") / 32768.0

    def transcribe_tail(
        self, options: TranscribeOptions, start_time: float = 0.0
    ) -> TranscriptResult:
        """Model the cached PCM's tail past ``start_time`` — no decode (CR-027 / D27)."""

        detected = "en" if options.translate else options.language
        pcm = self._pcm()
        start_sample = max(0, int(start_time * 16000))
        tail = pcm[start_sample:]
        if tail.shape[0] == 0:
            return TranscriptResult(text="", detected_language=detected, segments=[])
        return self._transcriber._transcribe_pcm(tail, options, offset=start_time)


def _decode_to_pcm(audio_bytes: bytes):  # noqa: ANN201 - returns np.ndarray
    """Decode container audio → a float32 16 kHz mono PCM ndarray (CR-013).

    This is what the streaming window slices by ``start_time`` so the model only sees
    the new tail. Decode is cheap relative to transcription, so decoding the whole
    buffer each window is fine — the D24 win is bounding the *model* pass. Reuses the
    WAV decode (ffmpeg binary or PyAV, CR-011) then reads it into a numpy array.
    """

    import wave

    import numpy as np

    wav_path = _decode_to_wav(audio_bytes)
    try:
        with wave.open(wav_path, "rb") as w:
            frames = w.readframes(w.getnframes())
        pcm = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
        return pcm
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def _decode_to_wav(audio_bytes: bytes) -> str:
    """Decode streamed container audio to 16 kHz mono WAV.

    CR-011: prefer the ``ffmpeg`` binary if it is on PATH (unchanged behaviour); else
    fall back to **PyAV** (``av``), which ``faster-whisper`` already installs and which
    embeds the ffmpeg libraries in-process — so real transcription works without a
    separately-installed ffmpeg executable. If neither is available, raise the typed
    :class:`UnsupportedMediaError` (failure is a state, not a crash).
    """

    import shutil

    if shutil.which("ffmpeg") is not None:
        return _decode_with_ffmpeg_binary(audio_bytes)

    try:
        import av  # noqa: F401  # decode detail of the real driver; inside drivers/ only
    except ImportError as exc:
        raise UnsupportedMediaError(
            "cannot decode a live audio stream: neither the ffmpeg binary nor PyAV "
            "(av) is available. Install ffmpeg, or install faster-whisper (which "
            "provides PyAV), or run with ACP_MODEL_DRIVER=fake."
        ) from exc
    return _decode_with_pyav(audio_bytes)


def _decode_with_ffmpeg_binary(audio_bytes: bytes) -> str:
    """The original ffmpeg-binary decode (CR-001); unchanged for hosts that have it."""

    in_fd, in_path = tempfile.mkstemp(suffix=".webm")
    with os.fdopen(in_fd, "wb") as fh:
        fh.write(audio_bytes)
    out_fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(out_fd)
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if os.path.exists(in_path):
            os.unlink(in_path)
    if proc.returncode != 0:
        if os.path.exists(out_path):
            os.unlink(out_path)
        raise UnsupportedMediaError(
            f"ffmpeg could not decode the audio stream: {proc.stderr.strip()[:500]}"
        )
    return out_path


def _decode_with_pyav(audio_bytes: bytes) -> str:
    """Decode container bytes → 16 kHz mono WAV using PyAV in-process (CR-011).

    Resamples every audio frame to signed-16 mono at 16 kHz (what the model expects)
    and writes a WAV. Any PyAV failure becomes the typed :class:`UnsupportedMediaError`.
    """

    import io

    import av

    out_fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(out_fd)
    try:
        in_container = av.open(io.BytesIO(audio_bytes))
        out_container = av.open(out_path, mode="w", format="wav")
        out_stream = out_container.add_stream("pcm_s16le", rate=16000)
        out_stream.layout = "mono"
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=16000
        )
        for frame in in_container.decode(audio=0):
            for rframe in resampler.resample(frame):
                for packet in out_stream.encode(rframe):
                    out_container.mux(packet)
        for packet in out_stream.encode(None):  # flush
            out_container.mux(packet)
        out_container.close()
        in_container.close()
    except Exception as exc:  # noqa: BLE001 - any PyAV failure → typed error, clean close
        if os.path.exists(out_path):
            os.unlink(out_path)
        raise UnsupportedMediaError(
            f"PyAV could not decode the audio stream: {str(exc)[:500]}"
        ) from exc
    return out_path
