"""Contract-level validation for the MHEAT OpenAPI spec.

Catches regressions where a router introduces an operation that breaks
OAS 3 compliance — e.g. duplicated operationId, malformed schemas,
path parameters without a declared parameter block, etc. Runs on every
test invocation, so no separate CI step is required.
"""

from __future__ import annotations

import pytest
from openapi_spec_validator import validate

# The full set of tags advertised in `main.py::_TAGS_METADATA`. Keeping the
# assertion explicit means a forgotten tag / rename is visible in diff review.
EXPECTED_TAGS = {"health", "events", "overlays", "stac", "anomaly", "processes", "ogcapi", "metrics"}

# Every path we document publicly. Paths not listed here are still allowed
# (we don't want to gate experimental endpoints) — but disappearance of any
# of these is a breaking change against the documented contract.
EXPECTED_PATHS = {
    "/api/health",
    "/api/readyz",
    "/api/events",
    "/api/events.csv",
    "/api/events.parquet",
    "/api/events/{event_id}/series",
    "/api/processes/mhw-detect",
    "/api/processes/mhw-detect/execution",
    "/api/processes/jobs",
    "/api/processes/jobs/{job_id}",
    "/api/processes/jobs/{job_id}/results",
    "/api/overlays/{kind}",
    "/api/anomaly",
    "/api/anomaly/extent",
    "/api/stac/collections",
    "/api/ogcapi",
    "/api/ogcapi/conformance",
    "/api/ogcapi/collections",
    "/api/metrics",
}


def test_openapi_spec_validates_as_oas3(client):
    spec = client.get("/api/openapi.json").json()
    # Raises if the spec is malformed against the OAS 3 meta-schema.
    validate(spec)


def test_openapi_declares_every_publicly_advertised_tag(client):
    spec = client.get("/api/openapi.json").json()
    declared = {t["name"] for t in spec.get("tags", [])}
    missing = EXPECTED_TAGS - declared
    assert not missing, f"Missing tags in OpenAPI: {sorted(missing)}"


def test_openapi_exposes_the_documented_paths(client):
    spec = client.get("/api/openapi.json").json()
    declared = set(spec.get("paths", {}).keys())
    missing = EXPECTED_PATHS - declared
    assert not missing, (
        "Paths documented in the README / CHANGELOG but missing from the "
        f"OpenAPI spec: {sorted(missing)}"
    )


def test_every_operation_has_a_summary(client):
    """A summary is required so auto-generated docs and client SDKs are readable."""
    spec = client.get("/api/openapi.json").json()
    missing: list[str] = []
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not op.get("summary"):
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"Operations without a summary: {missing}"


@pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
def test_path_parameters_are_declared(client, path: str):
    """Any ``{var}`` in a path must appear in the operation's parameters."""
    spec = client.get("/api/openapi.json").json()
    ops = spec["paths"][path]
    for method, op in ops.items():
        if method not in {"get", "post", "put", "patch", "delete"}:
            continue
        expected = {seg.strip("{}") for seg in path.split("/") if seg.startswith("{") and seg.endswith("}")}
        declared_names = {p["name"] for p in op.get("parameters", []) if p.get("in") == "path"}
        missing = expected - declared_names
        assert not missing, f"{method.upper()} {path} missing path params: {missing}"
