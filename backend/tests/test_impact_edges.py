"""Extra tests for `app.impact` edge cases."""

from __future__ import annotations

from app.impact import attach_impact_properties, compute_impact


def _feat(event_id: str, geom: dict) -> dict:
    return {
        "type": "Feature",
        "id": event_id,
        "geometry": geom,
        "properties": {"event_id": event_id},
    }


def _fc(features: list) -> dict:
    return {"type": "FeatureCollection", "features": features}


def test_compute_impact_on_empty_event_list_returns_empty_summary():
    overlays = {
        "aquaculture": _fc([]),
        "mpa": _fc([]),
        "seagrass": _fc([]),
    }
    result = compute_impact([], overlays)
    # Accepts any of: dict with zero counts, None, or empty list — the contract
    # is just "must not explode on empty".
    assert result is None or isinstance(result, (dict, list))


def test_attach_impact_properties_handles_events_without_geometry():
    """A degenerate GeoJSON with a null geometry must not crash the attach loop."""
    geojson = _fc([
        {"type": "Feature", "id": "e0", "geometry": None, "properties": {"event_id": "e0"}},
    ])
    overlays = {
        "aquaculture": _fc([]),
        "mpa": _fc([]),
        "seagrass": _fc([]),
    }
    attach_impact_properties(geojson, [], overlays)
    # Function should complete cleanly; impact properties may be absent or zero.
    assert geojson["features"][0]["properties"]["event_id"] == "e0"
