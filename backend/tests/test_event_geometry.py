"""Lock in the (Multi)Polygon / Point geometry behaviour added to
``MhwEvent`` and ``cluster_events``.

These tests cover :func:`app.mhw._union_pixel_geometry` and the
``geometry`` field of :meth:`app.mhw.MhwEvent.to_feature` — the new
shapely-driven cluster-shape rendering path. They are written without
hitting the FastAPI app (no client fixture) so they are fast and
unaffected by any substrate changes upstream.
"""

from __future__ import annotations

import pytest

from app.mhw import (
    CATEGORY_NAMES,
    MhwEvent,
    _union_pixel_geometry,
    cluster_events,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

# 0.0625° is the native CMS L4 SST grid step — keep test bbox sizes in
# line with what `_detect_cube_impl` actually emits per pixel so the test
# documents real-world cell geometry, not an arbitrary grid.
_HALF_STEP = 0.125


def _mk_pixel(
    eid: str,
    lon: float,
    lat: float,
    *,
    d0: str = "2022-07-01",
    d1: str = "2022-07-10",
    cat: int = 2,
    intensity: float = 1.5,
) -> MhwEvent:
    """Build a single-pixel MhwEvent at ``(lon, lat)`` with a 0.25°-wide cell."""
    return MhwEvent(
        event_id=eid,
        date_start=d0,
        date_end=d1,
        date_peak=d0,
        duration_days=10,
        intensity_max=intensity,
        intensity_mean=intensity * 0.6,
        intensity_cumulative=intensity * 5,
        category=cat,
        category_name=CATEGORY_NAMES[cat - 1],
        centroid_lon=lon,
        centroid_lat=lat,
        bbox=[
            lon - _HALF_STEP, lat - _HALF_STEP,
            lon + _HALF_STEP, lat + _HALF_STEP,
        ],
    )


# ---------------------------------------------------------------------
# _union_pixel_geometry — direct calls
# ---------------------------------------------------------------------
def test_union_pixel_geometry_single_pixel_emits_point() -> None:
    """A single-pixel cluster has no spatial extent worth drawing as a
    polygon — emit a Point at the pixel centroid so the frontend can
    render it as a circle marker."""
    px = _mk_pixel("a", lon=10.0, lat=41.0)
    geom = _union_pixel_geometry([px])
    assert geom is not None
    assert geom["type"] == "Point"
    # Centroid of the bbox = (10.0, 41.0)
    assert geom["coordinates"] == [10.0, 41.0]


def test_union_pixel_geometry_two_adjacent_pixels_yield_polygon() -> None:
    """Two pixels sharing an edge merge into a single rectangular Polygon."""
    p1 = _mk_pixel("a", lon=10.0, lat=41.0)
    # Adjacent in lon: bbox shares the x = 10.125 edge.
    p2 = _mk_pixel("b", lon=10.25, lat=41.0)
    geom = _union_pixel_geometry([p1, p2])
    assert geom is not None
    assert geom["type"] == "Polygon"

    coords = geom["coordinates"][0]
    xs = sorted({pt[0] for pt in coords})
    ys = sorted({pt[1] for pt in coords})
    # Union spans the full lon extent of both cells, full lat of one.
    assert xs[0] == pytest.approx(9.875)
    assert xs[-1] == pytest.approx(10.375)
    assert ys[0] == pytest.approx(40.875)
    assert ys[-1] == pytest.approx(41.125)
    # Closed ring → first == last.
    assert coords[0] == coords[-1]


def test_union_pixel_geometry_l_shape_yields_complex_polygon() -> None:
    """A 5-pixel L-shape is non-rectangular — its boundary needs more
    than the 4 corner points a rectangle has."""
    # L-shape:  X X X
    #           X
    #           X
    coords_in = [(10.0, 41.0), (10.25, 41.0), (10.5, 41.0),
                 (10.0, 41.25), (10.0, 41.5)]
    members = [_mk_pixel(f"p{i}", lon=lo, lat=la)
               for i, (lo, la) in enumerate(coords_in)]
    geom = _union_pixel_geometry(members)
    assert geom is not None
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    # An L-shape rendered by shapely traces an inside corner — it has
    # at minimum 7 distinct vertices (6 outer corners + the closing
    # repeat) and in practice 13 for our adjacency. Lock in "more than 4
    # boundary points" as the contract.
    assert len(ring) > 4


def test_union_pixel_geometry_disconnected_groups_yield_multipolygon() -> None:
    """Two pixel cells that share no edge stay as separate polygons in a
    MultiPolygon — the frontend can colour them as one cluster but they
    still render as physically distinct hot spots."""
    near = _mk_pixel("a", lon=10.0, lat=41.0)
    far = _mk_pixel("b", lon=20.0, lat=41.0)  # far away → no shared edge
    geom = _union_pixel_geometry([near, far])
    assert geom is not None
    assert geom["type"] == "MultiPolygon"
    # Two separate component polygons.
    assert len(geom["coordinates"]) == 2


def test_union_pixel_geometry_empty_input_returns_none() -> None:
    assert _union_pixel_geometry([]) is None


def test_union_pixel_geometry_missing_bboxes_returns_none() -> None:
    """If no member has a usable bbox the helper returns None and the
    caller falls back to the rectangle path in to_feature."""
    px = _mk_pixel("a", lon=10.0, lat=41.0)
    px.bbox = []
    # Two members so we don't trip the single-pixel Point shortcut.
    px2 = _mk_pixel("b", lon=10.25, lat=41.0)
    px2.bbox = []
    assert _union_pixel_geometry([px, px2]) is None


# ---------------------------------------------------------------------
# MhwEvent.to_feature — geometry-field round-trip
# ---------------------------------------------------------------------
def test_to_feature_uses_precomputed_geometry_when_set() -> None:
    """When ``MhwEvent.geometry`` is populated, ``to_feature`` must use
    it verbatim and ignore the bbox-rectangle fallback."""
    custom_geom = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    }
    e = MhwEvent(
        event_id="cluster-x",
        date_start="2022-07-01", date_end="2022-07-10", date_peak="2022-07-05",
        duration_days=10,
        intensity_max=2.0, intensity_mean=1.2, intensity_cumulative=12.0,
        category=2, category_name=CATEGORY_NAMES[1],
        centroid_lon=10.0, centroid_lat=41.0,
        bbox=[9.0, 40.0, 11.0, 42.0],
        n_pixels=4,
        geometry=custom_geom,
    )
    feat = e.to_feature()
    assert feat["geometry"] is custom_geom
    # Sanity: the bbox rectangle that would have been drawn instead is
    # NOT what landed in the feature.
    bbox_corners = {(9.0, 40.0), (11.0, 40.0), (11.0, 42.0), (9.0, 42.0)}
    geom_corners = {tuple(p) for p in feat["geometry"]["coordinates"][0]}
    assert geom_corners != bbox_corners


def test_to_feature_falls_back_to_bbox_when_no_geometry() -> None:
    """Per-pixel events (or any MhwEvent built without a precomputed
    geometry) must continue to render as the bbox rectangle — back-
    compatible with callers that pre-date the cluster-geometry change."""
    e = _mk_pixel("a", lon=10.0, lat=41.0)
    assert e.geometry is None
    feat = e.to_feature()
    assert feat["geometry"]["type"] == "Polygon"
    ring = feat["geometry"]["coordinates"][0]
    # The 4-corner bbox rectangle (closed → 5 entries).
    assert len(ring) == 5
    assert ring[0] == ring[-1]


# ---------------------------------------------------------------------
# cluster_events end-to-end — the geometry field is populated correctly
# ---------------------------------------------------------------------
def test_cluster_events_emits_point_for_isolated_pixel() -> None:
    """An isolated pixel (no neighbours) clusters to a 1-member group →
    cluster geometry should be a Point."""
    lone = _mk_pixel("only", lon=10.0, lat=41.0)
    clusters = cluster_events([lone])
    assert len(clusters) == 1
    geom = clusters[0].to_feature()["geometry"]
    assert geom["type"] == "Point"


def test_cluster_events_emits_polygon_for_adjacent_pair() -> None:
    p1 = _mk_pixel("a", lon=10.0, lat=41.0)
    p2 = _mk_pixel("b", lon=10.25, lat=41.0)
    clusters = cluster_events([p1, p2])
    assert len(clusters) == 1
    feat = clusters[0].to_feature()
    assert feat["geometry"]["type"] == "Polygon"
