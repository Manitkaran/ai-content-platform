"""M5.2 / M5.3 / M5.4 — SDKs exist and match the API; repo-hygiene files present."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in text
    assert "WITHOUT WARRANTY" in text


def test_gitignore_excludes_secrets_and_heavy_files():
    gi = (ROOT / ".gitignore").read_text()
    for needle in [".env", "data/", "weights/", "*.pt"]:
        assert needle in gi, f"{needle} not ignored"


def test_readme_run_guide_present():
    readme = (ROOT / "README.md").read_text()
    assert "docker compose up" in readme
    assert "/transcribe" in readme
    assert "MIT" in readme


def test_php_client_present_and_shaped():
    php = (ROOT / "clients" / "php" / "src" / "Client.php").read_text()
    assert "function transcribe" in php
    assert "function getJob" in php
    assert (ROOT / "clients" / "php" / "composer.json").exists()


def test_js_client_present_and_shaped():
    js = (ROOT / "clients" / "js" / "index.js").read_text()
    assert "async transcribe" in js
    assert "waitForResult" in js
    assert (ROOT / "clients" / "js" / "package.json").exists()
    assert (ROOT / "clients" / "js" / "index.d.ts").exists()


# CR-029: the SDKs must cover the post-M5 API surface (batch, subtitle editing,
# subtitle download-with-format, prompt templates) and the new transcribe options.


def test_php_client_covers_new_endpoints_and_options():
    php = (ROOT / "clients" / "php" / "src" / "Client.php").read_text()
    for method in (
        "function transcribeBatch",
        "function editSegments",
        "function downloadSubtitle",
        "function listTemplates",
        "function saveTemplate",
        "function deleteTemplate",
    ):
        assert method in php, f"PHP client missing {method}"
    for field in ("target_language", "prompt", "template"):
        assert field in php, f"PHP client missing option {field}"


def test_js_client_covers_new_endpoints_and_options():
    js = (ROOT / "clients" / "js" / "index.js").read_text()
    for method in (
        "transcribeBatch",
        "editSegments",
        "downloadSubtitle",
        "listTemplates",
        "saveTemplate",
        "deleteTemplate",
    ):
        assert method in js, f"JS client missing {method}"

    dts = (ROOT / "clients" / "js" / "index.d.ts").read_text()
    assert '"json"' in dts, "JS types missing the json output format"
    for field in ("target_language", "prompt", "template"):
        assert field in dts, f"JS types missing option {field}"


def test_openapi_export_written():
    # The exported schema exists and lists the public routes.
    import json

    schema = json.loads((ROOT / "openapi.json").read_text())
    assert "/transcribe" in schema["paths"]
    assert "/jobs/{job_id}" in schema["paths"]
