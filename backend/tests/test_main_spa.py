"""Tests for the FastAPI app wiring in `app.main` — API-only and SPA fallback."""

from __future__ import annotations


def test_root_returns_api_only_descriptor_when_no_frontend_build(client):
    """With FRONTEND_DIR pointing at a non-existent path, `/` returns the
    API-only JSON descriptor, not an HTML page."""
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "mheat"
    assert "version" in body
    assert body["docs"] == "/api/docs"
    assert body["frontend"] == "not_built"


def test_openapi_spec_is_served_under_api_prefix(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["info"]["title"] == "MHEAT API"
    assert spec["info"]["version"]
    # Sanity: the tagged groups documented in main.py are present.
    tag_names = {t["name"] for t in spec.get("tags", [])}
    assert {"health", "events", "overlays", "ogcapi"} <= tag_names
