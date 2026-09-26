"""FastAPI application factory (M0.1) + demo page mount (M6.1).

``create_app()`` builds the app so tests can construct isolated instances. The
module-level ``app`` is what uvicorn/Docker serve.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import __version__
from app.api.routes import router
from app.api.stream import stream_router
from app.config import get_settings

_STATIC_DIR = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    settings = get_settings()  # fails loudly here if config is invalid
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Self-hosted speech/video-to-text. Wraps an open-source STT model "
            "(Whisper family) behind a REST API. Submit media to POST /transcribe "
            "and poll GET /jobs/{id}."
        ),
    )
    app.include_router(router)
    app.include_router(stream_router)  # WS /transcribe/stream (CR-001 / D22)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def demo_page() -> str:  # M6.1: one evaluation page, a thin client of the API
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app


app = create_app()
