"""Tests for /api/stac/*."""

from __future__ import annotations


def test_stac_catalog_root_returns_valid_catalog(client):
    """STAC 1.0 §Catalog landing page — required for STAC client bootstrap."""
    r = client.get("/api/stac")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "Catalog"
    assert body["stac_version"].startswith("1.")
    assert body["id"]
    assert body["description"]
    rels = {link["rel"] for link in body["links"]}
    assert {"self", "root", "data"} <= rels
    assert any(link["rel"] == "child" for link in body["links"]), \
        "Catalog must advertise its child Collection(s)"


def test_stac_collections_root_returns_expected_shape(client):
    r = client.get("/api/stac/collections")
    assert r.status_code == 200
    body = r.json()
    assert "collections" in body
    colls = body["collections"]
    assert isinstance(colls, list) and colls, "at least one STAC Collection expected"
    col = colls[0]
    assert col["type"] == "Collection"
    assert col["stac_version"].startswith("1.")
    assert "id" in col and "license" in col and "extent" in col


def test_stac_collection_items_returns_feature_collection(client):
    # Discover the collection id dynamically — safer across repo edits.
    root = client.get("/api/stac/collections").json()
    col_id = root["collections"][0]["id"]
    r = client.get(f"/api/stac/collections/{col_id}/items")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)


def test_stac_unknown_collection_returns_404(client):
    r = client.get("/api/stac/collections/does-not-exist/items")
    assert r.status_code == 404
