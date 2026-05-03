"""Basic smoke tests for the health endpoints."""

from __future__ import annotations


def test_health_ok(client) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_ready_ok_with_substrate(client) -> None:
    """conftest pre-populates sst.zarr + climatology.zarr, so readyz is green."""
    r = client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["sst_cache_present"] is True
    assert body["climatology_present"] is True


def test_stac_collection(client) -> None:
    r = client.get("/api/stac/collections")
    assert r.status_code == 200
    body = r.json()
    assert body["collections"][0]["id"] == "mheat-med-mhw"


def test_overlays_aquaculture(client) -> None:
    """The conftest mock doesn't intercept WFS so this hits the live WFS or
    falls back to the bundled fixture; either way we get a FeatureCollection.
    """
    r = client.get("/api/overlays/aquaculture")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) > 0


def test_unknown_overlay_404(client) -> None:
    r = client.get("/api/overlays/ufos")
    assert r.status_code == 404
