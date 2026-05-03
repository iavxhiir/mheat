"""Contract-diff gate — live OpenAPI spec vs the committed baseline.

Catches accidental breaking changes: a PR that removes a documented path,
drops a request field, or narrows a response schema without bumping the
spec version will fail this test.

Intentional changes are accepted by regenerating the baseline:

    python scripts/freeze_openapi.py

The rule is one-way strict — new paths / new fields / new enum values in
the live spec are fine (additive). The check fires only when the baseline
has something the live spec has lost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

BASELINE_PATH = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.baseline.json"


@pytest.fixture()
def baseline() -> dict[str, Any]:
    if not BASELINE_PATH.is_file():
        pytest.skip(f"no baseline at {BASELINE_PATH}; run scripts/freeze_openapi.py")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def live_spec(client) -> dict[str, Any]:
    return client.get("/api/openapi.json").json()


def test_no_path_was_removed(baseline, live_spec):
    missing = sorted(set(baseline.get("paths", {})) - set(live_spec.get("paths", {})))
    assert not missing, (
        f"Paths removed vs baseline (breaking change): {missing}. "
        f"If intentional, bump the spec version + regenerate the baseline."
    )


def test_no_operation_was_removed(baseline, live_spec):
    """A removed HTTP verb on an existing path is a breaking change too."""
    live_paths = live_spec.get("paths", {})
    removed: list[str] = []
    for path, base_ops in baseline.get("paths", {}).items():
        if path not in live_paths:
            continue  # already caught by test_no_path_was_removed
        live_ops = set(live_paths[path])
        for method in base_ops:
            if method in {"parameters", "summary", "description"}:
                continue
            if method not in live_ops:
                removed.append(f"{method.upper()} {path}")
    assert not removed, f"Operations removed vs baseline: {removed}"


def test_no_required_path_parameter_was_renamed(baseline, live_spec):
    """A path template like ``/items/{id}`` must not silently become ``/items/{uid}``."""
    live_paths = live_spec.get("paths", {})
    drifted: list[str] = []
    for path, base_ops in baseline.get("paths", {}).items():
        if path not in live_paths:
            continue
        for method, op in base_ops.items():
            if method in {"parameters", "summary", "description"}:
                continue
            base_params = {
                p["name"] for p in op.get("parameters", [])
                if p.get("in") == "path" and p.get("required")
            }
            live_op = live_paths[path].get(method, {})
            live_params = {
                p["name"] for p in live_op.get("parameters", [])
                if p.get("in") == "path" and p.get("required")
            }
            missing = base_params - live_params
            if missing:
                drifted.append(f"{method.upper()} {path}: missing path params {missing}")
    assert not drifted, (
        "Required path parameters removed or renamed vs baseline:\n  "
        + "\n  ".join(drifted)
    )


def test_no_component_schema_was_removed(baseline, live_spec):
    live_schemas = set(live_spec.get("components", {}).get("schemas", {}))
    base_schemas = set(baseline.get("components", {}).get("schemas", {}))
    missing = sorted(base_schemas - live_schemas)
    assert not missing, f"Component schemas removed vs baseline: {missing}"


def test_no_required_field_was_removed_from_a_response_schema(baseline, live_spec):
    """Response-schema narrowing is a client-breaking change."""
    live_schemas = live_spec.get("components", {}).get("schemas", {})
    drifts: list[str] = []
    for name, base_schema in baseline.get("components", {}).get("schemas", {}).items():
        base_required = set(base_schema.get("required", []))
        if not base_required:
            continue
        live_schema = live_schemas.get(name, {})
        live_required = set(live_schema.get("required", []))
        missing = base_required - live_required
        if missing:
            drifts.append(f"{name}: required→optional or removed: {sorted(missing)}")
    assert not drifts, (
        "Required fields weakened vs baseline (response consumers may break):\n  "
        + "\n  ".join(drifts)
    )
