"""Contract tests asserting OpenAPI examples exist on key endpoints."""

from __future__ import annotations

import pytest


@pytest.fixture()
def spec(client):
    return client.get("/api/openapi.json").json()


def _param(spec, path, method, name):
    ops = spec["paths"][path][method]
    for p in ops.get("parameters", []):
        if p.get("name") == name:
            return p
    raise KeyError(f"{method.upper()} {path} has no parameter {name!r}")


def test_events_bbox_has_selectable_examples(spec):
    p = _param(spec, "/api/events", "get", "bbox")
    examples = p.get("examples") or p.get("schema", {}).get("examples") or {}
    assert examples, "bbox must expose openapi examples for Swagger UI"
    # At minimum the Adriatic + Mediterranean presets must be listed.
    keys = set(examples.keys()) if isinstance(examples, dict) else set()
    assert "adriatic" in keys
    assert "mediterranean" in keys


def test_events_min_category_has_severity_presets(spec):
    p = _param(spec, "/api/events", "get", "min_category")
    examples = p.get("examples") or {}
    assert set(examples.keys()) >= {"all", "severe_plus", "extreme_only"}


def test_mhw_detect_request_body_has_two_examples(spec):
    body = spec["paths"]["/api/processes/mhw-detect"]["post"].get("requestBody", {})
    content = body.get("content", {})
    schema_or_examples = content.get("application/json", {})
    # Examples may live on the media-type node or inside the schema.
    examples = schema_or_examples.get("examples")
    if not examples:
        schema = schema_or_examples.get("schema", {})
        # Resolve schema ref if present.
        ref = schema.get("$ref")
        if ref:
            key = ref.split("/")[-1]
            schema = spec["components"]["schemas"][key]
        examples = schema.get("examples", [])
    assert examples and len(examples) >= 2, "mhw-detect must document at least 2 example payloads"
