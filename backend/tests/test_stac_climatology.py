"""Tests for the climatology STAC Item.

Validates that the pre-computed Hobday baseline artifact is surfaced as a
first-class STAC Item when present, and absent (no item) when the artifact
is missing — without breaking the rest of the catalog endpoints.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.climatology import DOY_LEN, Climatology
from app.stac import COLLECTION_ID, build_climatology_item


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _tiny_climatology() -> Climatology:
    """Build a minimal but realistic Climatology for the catalog tests.

    Uses tiny spatial dimensions so the call is millisecond-fast even when
    the test suite is run in tight loops.
    """
    n_lat, n_lon = 3, 4
    seas = np.full((DOY_LEN, n_lat, n_lon), 19.0, dtype="float32")
    thresh = np.full((DOY_LEN, n_lat, n_lon), 22.0, dtype="float32")
    lats = np.linspace(38.0, 44.0, n_lat, dtype="float32")
    lons = np.linspace(-5.0, 35.0, n_lon, dtype="float32")
    attrs = {
        "clim_start": 1991,
        "clim_end": 2020,
        "bbox": [-6.0, 30.0, 36.5, 46.0],
        "source_dataset": "cmems_test_dataset",
        "grid_resolution": "0.05deg",
        "created_utc": "2026-04-25T00:00:00+00:00",
        "pctile": 90.0,
        "window_half_width": 5,
        "smooth_width": 31,
    }
    return Climatology.from_arrays(seas, thresh, lats, lons, attrs=attrs)


# ---------------------------------------------------------------------
# 1. Endpoint: climatology Item appears when artifact is present.
# ---------------------------------------------------------------------
def test_climatology_item_appears_when_artifact_present(client, monkeypatch):
    """With a climatology installed, /items must include the baseline Item.

    Schema check covers the load-bearing reviewer-visible fields: id,
    collection, type, datetime nullness, start/end ISO format, bbox, and the
    zarr asset.
    """
    clim = _tiny_climatology()
    monkeypatch.setattr(
        "app.sst.SSTProvider.load_climatology", lambda self: clim,
    )

    r = client.get(f"/api/stac/collections/{COLLECTION_ID}/items")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"

    expected_id = "mhw-climatology-mediterranean-1991-2020"
    matching = [it for it in body["features"] if it["id"] == expected_id]
    assert matching, (
        f"climatology item {expected_id!r} not found among "
        f"{[it['id'] for it in body['features']]}"
    )
    item = matching[0]

    assert item["type"] == "Feature"
    assert item["stac_version"] == "1.0.0"
    assert item["collection"] == COLLECTION_ID

    props = item["properties"]
    assert props["datetime"] is None
    assert props["start_datetime"] == "1991-01-01T00:00:00Z"
    assert props["end_datetime"] == "2020-12-31T23:59:59Z"
    assert props["mhw:percentile"] == 90.0
    assert props["mhw:window_half_width"] == 5
    assert props["mhw:smooth_width"] == 31
    assert "Hobday" in props["title"]
    assert isinstance(props.get("providers"), list) and props["providers"]

    assert item["bbox"] == [-6.0, 30.0, 36.5, 46.0]

    assets = item["assets"]
    assert "zarr" in assets
    assert assets["zarr"]["type"] == "application/vnd+zarr"
    assert "data" in assets["zarr"]["roles"]
    assert "documentation" in assets


# ---------------------------------------------------------------------
# 2. Endpoint: no climatology Item when artifact is missing.
# ---------------------------------------------------------------------
def test_climatology_item_absent_when_artifact_missing(client, monkeypatch):
    """``load_climatology`` returns None (demo / cold container) → no item.

    The existing per-year items must still be served — the climatology Item
    is purely additive.
    """
    monkeypatch.setattr(
        "app.sst.SSTProvider.load_climatology", lambda self: None,
    )

    r = client.get(f"/api/stac/collections/{COLLECTION_ID}/items")
    assert r.status_code == 200
    body = r.json()
    ids = [it["id"] for it in body["features"]]

    assert not any(i.startswith("mhw-climatology-") for i in ids), (
        f"climatology item leaked into the catalog when artifact missing: {ids}"
    )
    # Sanity: the rest of the collection is still populated.
    assert ids, "expected at least one non-climatology item to remain"


def test_climatology_item_fetchable_by_id(client, monkeypatch):
    """`/items/{id}` must serve the climatology Item directly when present."""
    clim = _tiny_climatology()
    monkeypatch.setattr(
        "app.sst.SSTProvider.load_climatology", lambda self: clim,
    )

    item_id = "mhw-climatology-mediterranean-1991-2020"
    r = client.get(f"/api/stac/collections/{COLLECTION_ID}/items/{item_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == item_id
    assert body["collection"] == COLLECTION_ID


# ---------------------------------------------------------------------
# 3. Direct unit test of build_climatology_item.
# ---------------------------------------------------------------------
def test_build_climatology_item_schema_minimum():
    """Direct schema validation against a tiny synthetic Climatology.

    Locks down the keys, types, and ISO datetime formats so a STAC validator
    would accept the output without complaint.
    """
    clim = _tiny_climatology()
    item = build_climatology_item(clim)

    # Top-level STAC 1.0.0 Item shape.
    assert item["type"] == "Feature"
    assert item["stac_version"] == "1.0.0"
    assert item["id"] == "mhw-climatology-mediterranean-1991-2020"
    assert item["collection"] == COLLECTION_ID

    # Geometry must be a closed Polygon ring (5 corners, first == last).
    geom = item["geometry"]
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert len(ring) == 5
    assert ring[0] == ring[-1]

    # bbox: 4 floats in (lon_min, lat_min, lon_max, lat_max) order.
    bbox = item["bbox"]
    assert isinstance(bbox, list) and len(bbox) == 4
    assert all(isinstance(v, float) for v in bbox)
    assert bbox[0] < bbox[2] and bbox[1] < bbox[3]

    # Properties: STAC requires either datetime or both start/end.
    props = item["properties"]
    assert props["datetime"] is None
    assert props["start_datetime"].endswith("Z")
    assert props["end_datetime"].endswith("Z")
    # ISO format with T separator.
    assert "T" in props["start_datetime"] and "T" in props["end_datetime"]
    # Ranges flow forward in time.
    assert props["start_datetime"] < props["end_datetime"]

    # Hobday provenance fields with correct types.
    assert isinstance(props["mhw:percentile"], (int, float))
    assert isinstance(props["mhw:window_half_width"], int)
    assert isinstance(props["mhw:smooth_width"], int)
    assert props["mhw:percentile"] == 90.0
    assert props["mhw:window_half_width"] == 5
    assert props["mhw:smooth_width"] == 31

    # Providers list is populated.
    providers = props["providers"]
    assert isinstance(providers, list) and len(providers) >= 1
    for p in providers:
        assert "name" in p and "roles" in p

    # Assets: zarr is the load-bearing data asset.
    assets = item["assets"]
    assert "zarr" in assets
    z = assets["zarr"]
    assert z["type"] == "application/vnd+zarr"
    assert "data" in z["roles"]
    assert "title" in z and z["title"]
    assert isinstance(z["href"], str) and z["href"]
    # documentation asset for human-readable context.
    assert "documentation" in assets
    assert assets["documentation"]["type"] == "text/html"
    assert assets["documentation"]["href"].endswith("/docs")

    # Links: self / parent / collection at minimum.
    rels = {link["rel"] for link in item["links"]}
    assert {"self", "parent", "collection"}.issubset(rels)


def test_build_climatology_item_falls_back_when_attrs_missing():
    """Missing optional attrs (e.g. legacy artifact without bbox) shouldn't
    crash — the function should derive a bbox from coordinates and supply
    safe defaults for Hobday knobs."""
    n_lat, n_lon = 2, 2
    seas = np.zeros((DOY_LEN, n_lat, n_lon), dtype="float32")
    thresh = np.zeros_like(seas)
    lats = np.array([40.0, 41.0], dtype="float32")
    lons = np.array([10.0, 11.0], dtype="float32")
    # Minimal attrs: only the years (no bbox, no Hobday knobs).
    clim = Climatology.from_arrays(
        seas, thresh, lats, lons,
        attrs={"clim_start": 1991, "clim_end": 2020},
    )
    item = build_climatology_item(clim)

    # bbox derived from coords.
    assert item["bbox"][0] == pytest.approx(10.0)
    assert item["bbox"][2] == pytest.approx(11.0)
    # Defaults applied for the Hobday knobs.
    assert item["properties"]["mhw:percentile"] == 90.0
    assert item["properties"]["mhw:window_half_width"] == 5
    assert item["properties"]["mhw:smooth_width"] == 31


# ---------------------------------------------------------------------
# 4. Collection extent widens to include the climatology when present.
# ---------------------------------------------------------------------
def test_collection_extent_includes_climatology_window(client, monkeypatch):
    """Adding the climatology Item should widen the collection's temporal
    extent (1991-2020 starts well before any of the seasonal items)."""
    clim = _tiny_climatology()
    monkeypatch.setattr(
        "app.sst.SSTProvider.load_climatology", lambda self: clim,
    )

    r = client.get(f"/api/stac/collections/{COLLECTION_ID}")
    assert r.status_code == 200
    body = r.json()
    interval = body["extent"]["temporal"]["interval"]
    assert interval and interval[0]
    start = interval[0][0]
    # Must reach back to at least 1991 (the climatology start).
    assert start.startswith("1991") or start < "1991-12-31", (
        f"collection temporal extent {start} did not widen to include "
        "the 1991-2020 climatology window"
    )
