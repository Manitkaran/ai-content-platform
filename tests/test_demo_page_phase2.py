"""CR-023 — the redesigned demo page carries the Phase-2 controls and stays a
thin client of the public API."""

from __future__ import annotations


def test_phase2_controls_present(client):
    html = client.get("/").text
    # CR-022 controls surfaced in the UI.
    assert 'id="template"' in html
    assert 'name="template"' in html
    assert 'id="prompt"' in html
    assert 'name="prompt"' in html
    # CR-023 drop-zone.
    assert "dropzone" in html.lower()


def test_phase2_loads_templates_from_public_api(client):
    html = client.get("/").text
    # The template picker is populated from the public GET /templates endpoint.
    assert "/templates" in html


def test_still_only_public_endpoints(client):
    html = client.get("/").text
    assert "/transcribe" in html
    assert "/jobs/" in html
    # No private/internal surface leaked into the client.
    assert "/internal" not in html
    assert "/_" not in html


def test_core_form_fields_survived_redesign(client):
    html = client.get("/").text
    assert "AI Content Platform" in html
    for field in ('name="file"', 'name="language"', 'name="translate"', 'name="output_format"'):
        assert field in html
