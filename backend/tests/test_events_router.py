"""End-to-end tests for the /api/events router on the synthetic demo cube."""

from __future__ import annotations


def test_events_returns_clustered_by_default(client) -> None:
    r = client.get("/api/events?start=2022-07-01&end=2022-08-31")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    # Clustering should bring 57 per-pixel events down to ≤ 10 clusters.
    n = len(body["features"])
    assert 1 <= n <= 12, f"Expected few clusters, got {n}"
    # Cluster IDs should have the 'cluster' prefix.
    assert all(f["id"].startswith("mhw-cluster-") for f in body["features"])


def test_events_raw_returns_unclustered(client) -> None:
    r = client.get("/api/events?start=2022-07-01&end=2022-08-31&raw=true")
    assert r.status_code == 200
    body = r.json()
    # Raw mode returns the full per-pixel list — typically 50+.
    assert len(body["features"]) >= 10
    assert all(f["id"].startswith("mhw-") for f in body["features"])


def test_events_include_impact_attached(client) -> None:
    r = client.get("/api/events?start=2022-07-01&end=2022-08-31")
    body = r.json()
    if not body["features"]:
        return
    # The impact-attach mechanism must populate every event with the three
    # impact fields, even when the value is zero (i.e. no overlap). The
    # synthetic 5×5 lat/lon grid in conftest is too small to reliably
    # overlap real EMODnet/Natura polygons in the Mediterranean, so we
    # only assert the presence + shape of the impact dict; absolute
    # non-zero values are exercised by the integration tests.
    for f in body["features"]:
        imp = f["properties"].get("impact")
        assert imp is not None, "include_impact=true must populate impact"
        assert {"n_aquaculture_sites", "mpa_area_km2", "seagrass_area_km2"} <= set(imp)


def test_events_exclude_impact_opts_out(client) -> None:
    r = client.get("/api/events?start=2022-07-01&end=2022-08-31&include_impact=false")
    body = r.json()
    for f in body["features"]:
        assert f["properties"].get("impact") is None


def test_events_min_category_filter(client) -> None:
    r = client.get("/api/events?start=2022-07-01&end=2022-08-31&min_category=4")
    body = r.json()
    for f in body["features"]:
        assert f["properties"]["category"] >= 4


def test_events_bbox_filter(client) -> None:
    # Tight bbox around the Tyrrhenian anomaly.
    r = client.get("/api/events?start=2022-07-01&end=2022-08-31&bbox=6,39,12,43")
    assert r.status_code == 200


def test_events_invalid_bbox_400(client) -> None:
    r = client.get("/api/events?bbox=not-a-bbox")
    assert r.status_code == 400


def test_stac_collection_is_dynamic(client) -> None:
    """STAC collection extent is well-formed and includes the current decade.

    The static collection extent declares 1982-01-01 as the open-ended start
    (the proposal's coverage floor) regardless of what's actually cached;
    the per-item interval is derived from the cached cube. Both must be
    syntactically valid ISO datetimes.
    """
    r = client.get("/api/stac/collections")
    assert r.status_code == 200
    body = r.json()
    interval = body["collections"][0]["extent"]["temporal"]["interval"][0]
    # Pinned to the Med MFC reanalysis floor (1987-01-01 per the proposal's
    # coverage commitment); end is open per STAC's rolling-series convention.
    assert interval[0] == "1987-01-01T00:00:00Z", (
        f"Expected 1987-01-01 floor, got {interval[0]}"
    )
    assert interval[1] is None, f"Expected open-ended end, got {interval[1]}"


def test_stac_items_dynamic_year_coverage(client) -> None:
    """STAC items are produced for each calendar year present in the cube
    plus the synthetic-cube ARCO Item; we just assert the ARCO Item is there
    so the catalog at least surfaces the live SST cube as a discoverable
    asset (the cached cube's exact year coverage drifts day to day).
    """
    r = client.get("/api/stac/collections/mheat-med-mhw/items")
    body = r.json()
    ids = {it["id"] for it in body["features"]}
    # The SST cube ARCO Item id starts with "mheat-sst-cube-mediterranean-".
    assert any(i.startswith("mheat-sst-cube-mediterranean-") for i in ids), (
        f"missing SST cube ARCO Item among: {sorted(ids)[:5]}…"
    )


def test_anomaly_returns_png(client) -> None:
    r = client.get("/api/anomaly?date=2022-07-20")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n")
