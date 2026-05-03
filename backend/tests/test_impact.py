"""Tests for the impact spatial-join helpers."""

from __future__ import annotations

from app.impact import attach_impact_properties, compute_impact
from app.mhw import CATEGORY_NAMES, MhwEvent, events_to_geojson


def _sample_overlays() -> dict:
    return {
        "aquaculture": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "inside"},
                    "geometry": {"type": "Point", "coordinates": [10.0, 41.0]},
                },
                {
                    "type": "Feature",
                    "properties": {"name": "outside"},
                    "geometry": {"type": "Point", "coordinates": [25.0, 41.0]},
                },
            ],
        },
        "mpa": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"sitecode": "X1"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[9.5, 40.5], [10.5, 40.5], [10.5, 41.5], [9.5, 41.5], [9.5, 40.5]]],
                    },
                }
            ],
        },
        "seagrass": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"habitat": "Posidonia"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[9.8, 40.8], [10.2, 40.8], [10.2, 41.2], [9.8, 41.2], [9.8, 40.8]]],
                    },
                }
            ],
        },
    }


def _event() -> MhwEvent:
    return MhwEvent(
        event_id="mhw-test-0001",
        date_start="2022-07-01",
        date_end="2022-07-15",
        date_peak="2022-07-08",
        duration_days=15,
        intensity_max=3.0,
        intensity_mean=2.0,
        intensity_cumulative=30.0,
        category=3,
        category_name=CATEGORY_NAMES[2],
        centroid_lon=10.0,
        centroid_lat=41.0,
        bbox=[9.0, 40.0, 11.0, 42.0],
        n_pixels=4,
    )


def test_compute_impact_counts_intersections() -> None:
    events = [_event()]
    overlays = _sample_overlays()
    result = compute_impact(events, overlays)
    assert result["summary"]["n_events"] == 1
    # One aquaculture inside, one outside → 1 counted.
    assert result["per_event"][0]["affected"]["aquaculture"] == 1
    assert result["per_event"][0]["affected"]["mpa"] == 1
    assert result["per_event"][0]["affected"]["seagrass"] == 1


def test_attach_impact_properties_mutates_geojson() -> None:
    events = [_event()]
    geojson = events_to_geojson(events)
    attach_impact_properties(geojson, events, _sample_overlays())
    imp = geojson["features"][0]["properties"]["impact"]
    assert imp["n_aquaculture_sites"] == 1
    assert imp["mpa_area_km2"] > 0
    assert imp["seagrass_area_km2"] > 0
    assert "summary" in imp


def test_attach_impact_skips_when_no_match() -> None:
    # Event far from overlays.
    e = _event()
    e.bbox = [-5.0, 35.0, -4.0, 36.0]
    e.centroid_lon, e.centroid_lat = -4.5, 35.5
    events = [e]
    geojson = events_to_geojson(events)
    attach_impact_properties(geojson, events, _sample_overlays())
    imp = geojson["features"][0]["properties"]["impact"]
    assert imp["n_aquaculture_sites"] == 0
    assert imp["mpa_area_km2"] == 0.0
    assert imp["seagrass_area_km2"] == 0.0
