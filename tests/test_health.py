"""M0.1 — health route + M6.1 demo page loads."""

from __future__ import annotations

from app.media import FfmpegStatus


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # CR-016: preflight reports FFmpeg presence; value is one of the enum members.
    assert body["ffmpeg"] in {"ok", "missing"}


def test_health_reports_ffmpeg_missing(client, monkeypatch):
    """CR-016: a missing FFmpeg is reported as ``missing`` but liveness stays up
    (still 200, status still ``ok``) — audio transcription does not need the CLI."""

    monkeypatch.setattr(
        "app.api.routes.ffmpeg_status", lambda: FfmpegStatus.missing
    )
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "ffmpeg": "missing"}


def test_demo_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Content Platform" in r.text
    # M6.1: the page must offer the option controls O10 puts in scope.
    assert 'name="file"' in r.text
    assert 'name="language"' in r.text
    assert 'name="translate"' in r.text
    assert 'name="output_format"' in r.text
