"""Tests for the OGC API - Features 1.0 endpoints."""

from __future__ import annotations


def test_ogcapi_landing(client):
    r = client.get("/api/ogcapi")
    assert r.status_code == 200
    j = r.json()
    assert "links" in j
    rels = {link["rel"] for link in j["links"]}
    assert {"self", "conformance", "data"} <= rels


def test_ogcapi_conformance(client):
    r = client.get("/api/ogcapi/conformance")
    assert r.status_code == 200
    j = r.json()
    assert "conformsTo" in j
    assert any("ogcapi-features-1" in c for c in j["conformsTo"])
    # Part 3 — queryables conformance.
    assert any("ogcapi-features-3" in c and "queryables" in c
               for c in j["conformsTo"])


def test_ogcapi_queryables_per_collection(client):
    """OGC API Features Part 3 — every collection advertises its queryables."""
    for cid in ("mhw-events", "aquaculture", "mpa", "seagrass"):
        r = client.get(f"/api/ogcapi/collections/{cid}/queryables")
        assert r.status_code == 200, f"{cid}: {r.status_code}"
        j = r.json()
        assert j["type"] == "object"
        assert j["$schema"].startswith("https://json-schema.org/")
        assert "properties" in j and j["properties"], f"{cid} has no properties"


def test_ogcapi_queryables_link_present_in_collection_doc(client):
    """The collection metadata must include a `queryables` link relation."""
    r = client.get("/api/ogcapi/collections/mhw-events")
    assert r.status_code == 200
    rels = {link["rel"] for link in r.json()["links"]}
    assert any("queryables" in rel for rel in rels), \
        f"queryables link missing; got rels={rels}"


def test_ogcapi_queryables_unknown_collection_404(client):
    r = client.get("/api/ogcapi/collections/does-not-exist/queryables")
    assert r.status_code == 404


def test_ogcapi_collections_list(client):
    r = client.get("/api/ogcapi/collections")
    assert r.status_code == 200
    j = r.json()
    ids = {c["id"] for c in j["collections"]}
    assert {"mhw-events", "aquaculture", "mpa", "seagrass"} <= ids


def test_ogcapi_collection_detail(client):
    r = client.get("/api/ogcapi/collections/mhw-events")
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "mhw-events"
    assert "extent" in j
    assert "links" in j


def test_ogcapi_items_paging(client):
    r = client.get("/api/ogcapi/collections/mhw-events/items?limit=2&offset=0")
    assert r.status_code == 200
    j = r.json()
    assert j["type"] == "FeatureCollection"
    assert j["numberReturned"] <= 2
    assert "numberMatched" in j
    # If we have more than 2 events, Link header should include next
    if j["numberMatched"] > 2:
        assert 'rel="next"' in r.headers.get("Link", "")


def test_ogcapi_items_bbox_filter(client):
    # Narrow bbox around the western Mediterranean
    r = client.get(
        "/api/ogcapi/collections/mhw-events/items?bbox=2,36,10,44&limit=100"
    )
    assert r.status_code == 200
    j = r.json()
    assert j["type"] == "FeatureCollection"


def test_ogcapi_single_feature(client):
    list_resp = client.get("/api/ogcapi/collections/mhw-events/items?limit=1")
    features = list_resp.json().get("features", [])
    if not features:
        return
    fid = features[0]["id"]
    r = client.get(f"/api/ogcapi/collections/mhw-events/items/{fid}")
    assert r.status_code == 200
    assert r.json().get("id") == fid


def test_ogcapi_unknown_collection(client):
    r = client.get("/api/ogcapi/collections/nonexistent")
    assert r.status_code == 404
