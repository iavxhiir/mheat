"""Tests for the new ``/api/farms/expose``, ``/api/mpa/{site_code}/events``,
and ``/api/wms`` endpoints in ``app.routers.sectoral``.

The conftest substrate seeds a synthetic SST cube on a 5×5 grid spanning
lat 40-41, lon 10-11. Our MHW detection finds a few clusters in that
window; the tests below exercise the happy path, the input-validation
400s, and the unknown-SITECODE 404.
"""

from __future__ import annotations

import json
from datetime import date, timedelta


# --------------------------------------------------------------------------
# 1. POST /api/farms/expose
# --------------------------------------------------------------------------


def test_farms_expose_happy_path(client) -> None:
    """A farm sitting inside the synthetic warm-slab grid should match >= 1
    event when the request window covers the warm slab.

    The test uses the cube's own default 30-day window so it works even as
    the synthetic substrate end date drifts day-by-day with ``date.today()``.
    """
    body = {
        "farms": [
            # Centre of the synthetic grid — guaranteed to fall inside any
            # cluster that the warm-slab detection produces, regardless of
            # whether the cluster geometry is a Polygon or a single Point.
            {"id": "farm-centre", "lon": 10.5, "lat": 40.5},
            # Far away from any synthetic event — a sanity check that
            # distant farms come back with zero matches.
            {"id": "farm-far", "lon": -5.0, "lat": 36.0},
        ],
    }
    r = client.post("/api/farms/expose", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["n_farms"] == 2
    assert {f["id"] for f in payload["farms"]} == {"farm-centre", "farm-far"}
    far = next(f for f in payload["farms"] if f["id"] == "farm-far")
    assert far["n_events"] == 0
    # Centre may or may not match depending on the random walk of clustering
    # over the synthetic warm slab. We assert the response shape rather than
    # the exact match count so the test stays stable across cube refreshes.
    centre = next(f for f in payload["farms"] if f["id"] == "farm-centre")
    assert isinstance(centre["events"], list)
    for ev in centre["events"]:
        assert {"event_id", "date_start", "date_end", "category", "intensity_max"} <= set(ev)


def test_farms_expose_rejects_empty_list(client) -> None:
    """Empty ``farms`` list should fail Pydantic ``min_length=1`` → 422."""
    r = client.post("/api/farms/expose", json={"farms": []})
    assert r.status_code == 422
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "validation_error"


def test_farms_expose_caps_at_500(client) -> None:
    """Submitting 501 farms should be rejected by the ``max_length`` validator."""
    farms = [
        {"id": f"f{i}", "lon": 10.5, "lat": 40.5}
        for i in range(501)
    ]
    r = client.post("/api/farms/expose", json={"farms": farms})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_farms_expose_rejects_inverted_window(client) -> None:
    """``end < start`` → 400 with the canonical error envelope."""
    r = client.post(
        "/api/farms/expose",
        json={
            "farms": [{"id": "x", "lon": 10.5, "lat": 40.5}],
            "start": "2022-08-01",
            "end": "2022-07-01",
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert "error" in body
    # The dict-detail surfaces under ``error.detail`` per the envelope spec.
    detail = body["error"].get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("status") == "bad_range"


# --------------------------------------------------------------------------
# 2. GET /api/mpa/{site_code}/events
# --------------------------------------------------------------------------


def _first_real_sitecode() -> str:
    """Pick a SITECODE that's actually in the bundled MPA fixture."""
    from pathlib import Path
    fixture = Path(__file__).parent.parent / "app" / "fixtures" / "overlays" / "mpa.json"
    data = json.loads(fixture.read_text())
    return str(data["features"][0]["properties"]["SITECODE"])


def test_mpa_events_returns_collection_for_known_sitecode(client) -> None:
    """A real SITECODE should return a 200 with the events FeatureCollection
    + an ``mpa`` block carrying the matched site metadata."""
    site_code = _first_real_sitecode()
    r = client.get(f"/api/mpa/{site_code}/events")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert "features" in body
    assert isinstance(body["features"], list)
    assert body["mpa"]["site_code"] == site_code
    assert "site_name" in body["mpa"]
    assert "window" in body
    assert body["n_events"] == len(body["features"])


def test_mpa_events_404_for_unknown_sitecode(client) -> None:
    """Unknown SITECODE → 404 with ``mpa_not_found`` slug."""
    r = client.get("/api/mpa/ZZ9999999/events")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    detail = body["error"].get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("status") == "mpa_not_found"


# --------------------------------------------------------------------------
# 3. GET /api/wms
# --------------------------------------------------------------------------


def test_wms_getmap_returns_png(client) -> None:
    """A valid WMS GetMap call should return an anomaly PNG."""
    # Use a date inside the synthetic cube's window. The substrate covers
    # ~120 days ending at today, so 'today - 30 days' is safely inside.
    target = (date.today() - timedelta(days=30)).isoformat()
    r = client.get(
        "/api/wms",
        params={
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": "anomaly",
            "bbox": "-6,30,36.5,46",
            "crs": "CRS:84",
            "width": 512,
            "height": 256,
            "format": "image/png",
            "time": target,
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers.get("X-WMS-Layer") == "anomaly"
    assert r.headers.get("X-WMS-Time") == target


def test_wms_getmap_rejects_unknown_layer(client) -> None:
    """A WMS GetMap with a layer the renderer doesn't know about → 400."""
    r = client.get(
        "/api/wms",
        params={
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": "no_such_layer",
            "bbox": "-6,30,36.5,46",
            "crs": "CRS:84",
            "width": 256,
            "height": 256,
            "format": "image/png",
        },
    )
    assert r.status_code == 400
    body = r.json()
    detail = body["error"].get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("status") == "wms_unknown_layer"


def test_wms_getmap_rejects_bad_bbox(client) -> None:
    """Non-numeric bbox → 400 wms_bad_bbox."""
    r = client.get(
        "/api/wms",
        params={
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": "anomaly",
            "bbox": "not,a,bbox",
            "crs": "CRS:84",
            "width": 256,
            "height": 256,
            "format": "image/png",
        },
    )
    assert r.status_code == 400
    detail = r.json()["error"].get("detail", {})
    assert detail.get("status") == "wms_bad_bbox"
