"""Gated video→transcript integration test (proves D8/M2.5 end-to-end).

This is the one path the GPU-free suite can't cover on a bare host: real FFmpeg
video→audio extraction feeding a job through to a subtitle export. It is:

  * **marked ``video_integration`` and DESELECTED** from the default suite
    (pytest.ini), and
  * **skipped** unless the ``ffmpeg`` binary is actually present,

so it stays green in Docker/CI (where the image installs ffmpeg) and skips cleanly
where ffmpeg is absent — instead of the video feature being merely *claimed*.

It deliberately uses the **fake transcriber** (the default), not the real model:
the thing under test is FFmpeg extraction (`app/media.py`), which is independent of
the model, so this needs no GPU, no weights, and no network. Run it with:

    pytest -m video_integration

The tiny silent MP4 fixture is synthesised at runtime with ffmpeg — nothing binary
is checked into the repo.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.video_integration

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _make_silent_mp4(path: str) -> None:
    """Synthesise ~0.5 s of silent H.264/AAC MP4 with ffmpeg (test fixture)."""

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-f", "lavfi", "-i", "color=c=black:s=32x32:r=5",
            "-t", "0.5",
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            path,
        ],
        capture_output=True,
        check=True,
    )


@pytest.fixture
def video_client(monkeypatch, tmp_path):
    """A TestClient on the fake transcriber but the REAL local storage + media path.

    Same isolation shape as conftest, but explicit here because this test is
    gated/deselected and may run standalone via ``-m video_integration``.
    """

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)  # conftest's autouse fixture may have made it
    env = {
        "ACP_ENV": "testing",
        "ACP_MODEL_DRIVER": "fake",  # video path is model-independent
        "ACP_STORAGE_DRIVER": "local",
        "ACP_DATA_DIR": str(data_dir),
        "ACP_DATABASE_URL": f"sqlite:///{data_dir}/acp.db",
        "ACP_TRANSCRIPT_RETENTION_DAYS": "30",
        "ACP_MAX_UPLOAD_BYTES": str(500 * 1024 * 1024),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from app.config import reset_settings_cache
    from app.jobs.store import reset_job_store_cache
    from app.model.registry import reset_transcriber_cache
    from app.storage.registry import reset_storage_cache

    reset_settings_cache()
    reset_job_store_cache()
    reset_transcriber_cache()
    reset_storage_cache()

    import app.main as main_module

    importlib.reload(main_module)
    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg binary not installed")
def test_video_upload_extracts_and_transcribes(video_client, tmp_path):
    """A real MP4 → FFmpeg extraction → completed job → subtitle export (D8/M2.5)."""

    mp4_path = tmp_path / "clip.mp4"
    _make_silent_mp4(str(mp4_path))
    assert mp4_path.stat().st_size > 0

    with mp4_path.open("rb") as fh:
        sub = video_client.post(
            "/transcribe",
            files={"file": ("clip.mp4", fh, "video/mp4")},
            data={"output_format": "srt"},
        )
    assert sub.status_code == 202
    job_id = sub.json()["id"]

    body = video_client.get(f"/jobs/{job_id}").json()
    # The point: video did NOT fail with unsupported_media — extraction worked and
    # the fake transcriber ran over the extracted audio to a completed job.
    assert body["status"] == "completed", body.get("error")

    # Video subtitle export: the completed video job renders SRT and VTT like any job.
    srt = video_client.get(f"/jobs/{job_id}/subtitle?format=srt")
    assert srt.status_code == 200
    vtt = video_client.get(f"/jobs/{job_id}/subtitle?format=vtt")
    assert vtt.status_code == 200
    assert vtt.text.startswith("WEBVTT")


@pytest.mark.skipif(_HAS_FFMPEG, reason="only meaningful when ffmpeg is absent")
def test_video_without_ffmpeg_is_failed_not_crash(video_client):
    """Sibling guarantee: with no ffmpeg, a video upload becomes a *failed* job with
    the typed ``unsupported_media`` code — never a 500 (D8 / 'failure is a state')."""

    fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    sub = video_client.post(
        "/transcribe",
        files={"file": ("clip.mp4", fake_mp4, "video/mp4")},
        data={},
    )
    assert sub.status_code == 202
    body = video_client.get(f"/jobs/{sub.json()['id']}").json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "unsupported_media"
    assert video_client.get("/health").status_code == 200
