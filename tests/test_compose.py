"""M0.2 / M0.4 — compose hygiene: no undocumented ${VAR}; non-root; one image."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
GPU_COMPOSE = ROOT / "docker-compose.gpu.yml"
ENV_EXAMPLE = ROOT / ".env.example"
DOCKERFILE = ROOT / "Dockerfile"


def _documented_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=", line.strip())
        if m:
            keys.add(m.group(1))
    return keys


def test_every_compose_var_is_documented():
    referenced = set(re.findall(r"\$\{([A-Z0-9_]+)\}", COMPOSE.read_text()))
    undocumented = referenced - _documented_keys()
    assert undocumented == set(), f"undocumented ${{VAR}} in compose: {undocumented}"


def test_compose_defines_app_and_worker_from_one_image():
    text = COMPOSE.read_text()
    assert "app:" in text and "worker:" in text
    # both build the same image (one image, command differs)
    assert text.count("ai-content-platform:latest") >= 2
    assert "app.worker" in text  # worker runs a different command


def test_dockerfile_runs_non_root():
    text = DOCKERFILE.read_text()
    assert "USER acp" in text
    assert "ffmpeg" in text  # FFmpeg declared for video input (M2.5)


def test_gpu_override_parses_and_selects_cuda():
    text = GPU_COMPOSE.read_text()
    assert "cuda" in text
    assert "nvidia" in text
