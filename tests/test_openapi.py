"""M5.1 — OpenAPI accuracy + M6.1 demo uses only public routes."""

from __future__ import annotations


def test_openapi_lists_public_routes(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/transcribe" in paths
    assert "/jobs/{job_id}" in paths
    # every public route carries a summary/description (M5.1: fail on undocumented)
    for path, methods in paths.items():
        for method, op in methods.items():
            assert op.get("summary") or op.get("description"), (
                f"{method.upper()} {path} has no summary/description"
            )


def test_error_shapes_documented(client):
    schema = client.get("/openapi.json").json()
    transcribe = schema["paths"]["/transcribe"]["post"]["responses"]
    assert "413" in transcribe
    assert "415" in transcribe
    job = schema["paths"]["/jobs/{job_id}"]["get"]["responses"]
    assert "404" in job


def test_docs_served(client):
    assert client.get("/docs").status_code == 200


def test_demo_calls_only_public_api(client):
    """The demo page must call no private/back-channel endpoint (M6.1)."""

    html = client.get("/").text
    # It should reference the public endpoints…
    assert "/transcribe" in html
    assert "/jobs/" in html
    # …and nothing that looks like a private/internal route.
    assert "/internal" not in html
    assert "/_" not in html
