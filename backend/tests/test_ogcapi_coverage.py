"""Additional OGC API — Features tests — covers pagination, filtering,
single-feature lookup and error paths. Targets the branches that
`test_ogcapi.py` doesn't already exercise.
"""

from __future__ import annotations


ITEMS_PATH = "/api/ogcapi/collections/{cid}/items"


def test_unknown_collection_returns_404(client):
    r = client.get("/api/ogcapi/collections/imaginary")
    assert r.status_code == 404
    r = client.get(ITEMS_PATH.format(cid="imaginary"))
    assert r.status_code == 404
    r = client.get(ITEMS_PATH.format(cid="imaginary") + "/abc")
    assert r.status_code == 404


def test_items_limit_and_offset_pagination(client):
    """Pagination emits next/prev links correctly."""
    r1 = client.get(ITEMS_PATH.format(cid="mpa") + "?limit=1&offset=0")
    assert r1.status_code == 200
    body = r1.json()
    assert body["type"] == "FeatureCollection"
    assert body["numberReturned"] == len(body["features"]) <= 1
    total = body["numberMatched"]
    # A next link only when more pages exist.
    rels = {l["rel"] for l in body["links"]}
    if total > 1:
        assert "next" in rels
        # Link header also carries next.
        assert 'rel="next"' in r1.headers.get("Link", "")
    # Prev should not appear on the first page.
    assert "prev" not in rels

    if total > 1:
        r2 = client.get(ITEMS_PATH.format(cid="mpa") + "?limit=1&offset=1")
        body2 = r2.json()
        rels2 = {l["rel"] for l in body2["links"]}
        assert "prev" in rels2


def test_items_bbox_filter_reduces_matches(client):
    """A tiny bbox must yield fewer or equal features than no filter."""
    full = client.get(ITEMS_PATH.format(cid="mpa")).json()
    narrow = client.get(
        ITEMS_PATH.format(cid="mpa") + "?bbox=0,0,0.1,0.1",
    ).json()
    assert narrow["numberMatched"] <= full["numberMatched"]


def test_items_invalid_bbox_returns_400(client):
    r = client.get(ITEMS_PATH.format(cid="mpa") + "?bbox=not-a-bbox")
    assert r.status_code == 400


def test_items_invalid_datetime_returns_400(client):
    r = client.get(ITEMS_PATH.format(cid="mhw-events") + "?datetime=not-a-date")
    assert r.status_code == 400


def test_items_datetime_interval_instant_and_open(client):
    """OGC datetime param accepts instant, interval, and open intervals."""
    for q in (
        "datetime=2022-07-20",
        "datetime=2022-07-01/2022-08-15",
        "datetime=2022-07-01/..",
        "datetime=../2022-08-15",
    ):
        r = client.get(ITEMS_PATH.format(cid="mhw-events") + f"?{q}")
        assert r.status_code == 200, f"{q} failed with {r.status_code}"


def test_get_single_feature_roundtrip_on_mhw_events(client):
    page = client.get(ITEMS_PATH.format(cid="mhw-events") + "?limit=1").json()
    feats = page.get("features") or []
    if not feats:
        # No events under the demo cube — just confirm 404 on a fake id.
        r = client.get(ITEMS_PATH.format(cid="mhw-events") + "/nope")
        assert r.status_code == 404
        return
    fid = feats[0]["id"]
    r = client.get(ITEMS_PATH.format(cid="mhw-events") + f"/{fid}")
    assert r.status_code == 200
    body = r.json()
    assert body.get("id") == fid
    assert body["type"] == "Feature"


def test_get_single_feature_404_for_unknown_id(client):
    r = client.get(ITEMS_PATH.format(cid="mpa") + "/does-not-exist-99999")
    assert r.status_code == 404


def test_landing_page_and_conformance(client):
    landing = client.get("/api/ogcapi").json()
    assert "links" in landing
    conf = client.get("/api/ogcapi/conformance").json()
    classes = conf.get("conformsTo", [])
    assert any("ogcapi-features-1/1.0/conf/core" in c for c in classes)
    assert any("ogcapi-features-1/1.0/conf/geojson" in c for c in classes)


def test_collection_detail_contains_links(client):
    r = client.get("/api/ogcapi/collections/mhw-events")
    assert r.status_code == 200
    body = r.json()
    rels = {l.get("rel") for l in body.get("links", [])}
    # Self + items links at minimum.
    assert "self" in rels
    assert "items" in rels
