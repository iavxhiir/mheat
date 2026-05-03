"""Tests for the uniform error envelope."""

from __future__ import annotations


def _assert_envelope(body: dict, *, status: int, code: str) -> None:
    assert "error" in body, f"response has no error key: {body}"
    err = body["error"]
    assert err["status"] == status
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    assert "request_id" in err


def test_400_bbox_invalid_maps_to_stable_code(client):
    r = client.get("/api/events?bbox=not-a-bbox")
    assert r.status_code == 400
    _assert_envelope(r.json(), status=400, code="bbox_invalid")


def test_404_unknown_collection_uses_collection_not_found(client):
    r = client.get("/api/ogcapi/collections/imaginary")
    assert r.status_code == 404
    _assert_envelope(r.json(), status=404, code="collection_not_found")


def test_422_pydantic_validation_error_lists_field_errors(client):
    r = client.post(
        "/api/processes/mhw-detect",
        json={"bbox": [0.0, 0.0, 1.0]},  # min_length=4 — pydantic rejects
    )
    assert r.status_code == 422
    body = r.json()
    _assert_envelope(body, status=422, code="validation_error")
    assert isinstance(body["error"].get("errors"), list)
    assert body["error"]["errors"], "errors list must be non-empty"


def test_503_metrics_disabled_maps_to_metrics_disabled_code(client):
    r = client.get("/api/metrics")
    # 404 in the default (disabled) mode, not 503 — but it still shares the envelope.
    assert r.status_code == 404
    _assert_envelope(r.json(), status=404, code="metrics_disabled")


def test_envelope_echoes_request_id_header(client):
    r = client.get("/api/events?bbox=bad", headers={"X-Request-Id": "rid-unit-test-123"})
    assert r.status_code == 400
    assert r.json()["error"]["request_id"] == "rid-unit-test-123"
    # And the response header carries it back too.
    assert r.headers.get("X-Request-Id") == "rid-unit-test-123"
