"""Tests for /api/processes and /api/processes/mhw-detect."""

from __future__ import annotations


def test_processes_listing_contains_mhw_detect(client):
    r = client.get("/api/processes")
    assert r.status_code == 200
    body = r.json()
    ids = [p["id"] for p in body.get("processes", [])]
    assert "mhw-detect" in ids


def test_mhw_detect_with_impact_returns_geojson_and_impact_payload(client):
    r = client.post(
        "/api/processes/mhw-detect",
        json={
            "bbox": [6.0, 38.0, 14.0, 43.0],
            "start": "2022-07-01",
            "end": "2022-08-15",
            "min_category": 1,
            "with_impact": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "successful"
    assert body["n_events"] == len(body["events"]["features"])
    assert body["events"]["type"] == "FeatureCollection"
    # with_impact=True should attach an impact envelope.
    assert body.get("impact") is not None


def test_mhw_detect_without_impact_returns_none_impact(client):
    r = client.post(
        "/api/processes/mhw-detect",
        json={"start": "2022-07-01", "end": "2022-08-15", "with_impact": False},
    )
    assert r.status_code == 200
    assert r.json().get("impact") is None


def test_mhw_detect_rejects_invalid_bbox_length(client):
    r = client.post(
        "/api/processes/mhw-detect",
        json={"bbox": [0.0, 0.0, 1.0]},  # only 3 values — pydantic min_length=4
    )
    assert r.status_code == 422


def test_mhw_detect_rejects_category_out_of_range(client):
    r = client.post(
        "/api/processes/mhw-detect",
        json={"min_category": 99},
    )
    assert r.status_code == 422
