# M0.2 / M0.4 — one image for both the API and the worker (command differs).
# CPU-first (D13). FFmpeg is a system dependency for video input (D8/M2.5).
# Model weights are NOT baked in (large; D5) — they live in a mounted volume and
# are fetched on first run (see docker-compose.yml + README).

FROM python:3.12-slim AS base

# System deps: ffmpeg for video audio-extraction; libgomp for faster-whisper CPU.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ACP_DATA_DIR=/data \
    ACP_DATABASE_URL=sqlite:////data/acp.db \
    HF_HOME=/weights

WORKDIR /app

# Install Python deps first for layer caching. The real model + translate drivers
# are optional; install them in the image so the shipped image can run real
# transcription and real translation (ACP_TRANSLATE_DRIVER=argos, the default).
# CR-025: language-pair packages are still downloaded at runtime (see .env.example).
COPY requirements.txt requirements-model.txt requirements-translate.txt ./
RUN pip install -r requirements.txt \
    && pip install -r requirements-model.txt \
    && pip install -r requirements-translate.txt

COPY app ./app

# Non-root runtime user; it owns the writable paths (data + weights volumes).
RUN useradd --create-home --uid 10001 acp \
    && mkdir -p /data /weights \
    && chown -R acp:acp /app /data /weights
USER acp

EXPOSE 8000

# Default command = the API. The worker service overrides this (see compose).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
