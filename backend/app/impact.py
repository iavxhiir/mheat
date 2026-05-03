"""Spatial join of MHW events against sectoral overlays → impact metrics."""

from __future__ import annotations

import logging
import math
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .mhw import MhwEvent
from .telemetry import span

logger = logging.getLogger(__name__)


# Mean Mediterranean latitude ≈ 40°; 1° lat ≈ 111 km,
# 1° lon at lat ≈ 111 km * cos(lat).
def _deg2_to_km2(geom: BaseGeometry) -> float:
    """Approximate polygon area (deg²) → km² at its own centroid latitude."""
    try:
        c = geom.centroid
        lat_rad = math.radians(c.y)
        km_per_deg_lat = 111.32
        km_per_deg_lon = 111.32 * math.cos(lat_rad)
        # For a polygon whose area is reported in deg², each "deg²" contributes
        # km_per_deg_lat * km_per_deg_lon km² (near-rectangular approximation).
        return float(geom.area * km_per_deg_lat * km_per_deg_lon)
    except Exception:  # noqa: BLE001
        return 0.0


def _parse_overlays(overlays: dict[str, dict[str, Any]]) -> dict[str, list[tuple[dict[str, Any], BaseGeometry]]]:
    """Parse each overlay FeatureCollection once into (props, geom) pairs."""
    parsed: dict[str, list[tuple[dict[str, Any], BaseGeometry]]] = {}
    for kind, fc in overlays.items():
        items: list[tuple[dict[str, Any], BaseGeometry]] = []
        for feat in fc.get("features", []):
            try:
                geom = shape(feat["geometry"])
            except Exception:  # noqa: BLE001
                continue
            items.append((feat.get("properties", {}), geom))
        parsed[kind] = items
    return parsed


def attach_impact_properties(
    geojson: dict[str, Any],
    events: list[MhwEvent],
    overlays: dict[str, dict[str, Any]],
) -> None:
    """Mutate the GeoJSON in place to attach a per-feature ``impact`` object.

    Each feature gains:
    * ``n_aquaculture_sites`` (int)
    * ``mpa_area_km2`` (float)  — area of intersecting MPA polygons clipped
      to the event bbox
    * ``seagrass_area_km2`` (float)
    * ``impact_summary`` — a short human-readable line
    """
    parsed = _parse_overlays(overlays)
    by_id: dict[str, BaseGeometry] = {e.event_id: shape(_event_geom_dict(e)) for e in events}

    for feat in geojson.get("features", []):
        eid = feat.get("id") or feat.get("properties", {}).get("event_id")
        geom = by_id.get(eid)
        if geom is None:
            continue
        n_aqua = 0
        mpa_area = 0.0
        sg_area = 0.0
        for _props, g in parsed.get("aquaculture", []):
            try:
                if geom.intersects(g):
                    n_aqua += 1
            except Exception:  # noqa: BLE001
                continue
        for _props, g in parsed.get("mpa", []):
            try:
                if geom.intersects(g):
                    inter = geom.intersection(g)
                    mpa_area += _deg2_to_km2(inter)
            except Exception:  # noqa: BLE001
                continue
        for _props, g in parsed.get("seagrass", []):
            try:
                if geom.intersects(g):
                    inter = geom.intersection(g)
                    sg_area += _deg2_to_km2(inter)
            except Exception:  # noqa: BLE001
                continue

        impact: dict[str, Any] = {
            "n_aquaculture_sites": n_aqua,
            "mpa_area_km2": round(mpa_area, 2),
            "seagrass_area_km2": round(sg_area, 2),
        }
        impact["summary"] = (
            f"{n_aqua} aquaculture site(s), {impact['mpa_area_km2']} km² MPA, "
            f"{impact['seagrass_area_km2']} km² seagrass"
        )
        props: dict[str, Any] = feat.setdefault("properties", {})
        props["impact"] = impact


def _event_geom_dict(e: MhwEvent) -> dict[str, Any]:
    """Plain GeoJSON geometry dict for an event (cluster or per-pixel)."""
    return e.to_feature()["geometry"]


def _event_geom(e: MhwEvent) -> BaseGeometry:
    """Event bbox → shapely polygon."""
    return shape(e.to_feature()["geometry"])


def compute_impact(
    events: list[MhwEvent],
    overlays: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Count overlay features intersecting each event.

    Args:
        events: MhwEvent list (already filtered to the time window of interest).
        overlays: mapping of overlay kind → GeoJSON FeatureCollection.

    Returns:
        Dict with a per-event breakdown and a global summary.
    """
    with span("compute_impact", n_events=len(events)):
        return _compute_impact_impl(events, overlays)


def _compute_impact_impl(events, overlays):
    # Pre-parse overlay geometries once.
    parsed: dict[str, list[tuple[dict[str, Any], BaseGeometry]]] = {}
    for kind, fc in overlays.items():
        items: list[tuple[dict[str, Any], BaseGeometry]] = []
        for feat in fc.get("features", []):
            try:
                geom = shape(feat["geometry"])
            except Exception:  # noqa: BLE001
                continue
            items.append((feat.get("properties", {}), geom))
        parsed[kind] = items

    per_event: list[dict[str, Any]] = []
    totals = dict.fromkeys(overlays, 0)
    for e in events:
        geom = _event_geom(e)
        hits: dict[str, list[dict[str, Any]]] = {k: [] for k in overlays}
        for kind, items in parsed.items():
            for props, g in items:
                try:
                    if geom.intersects(g):
                        hits[kind].append(props)
                except Exception:  # noqa: BLE001
                    continue
            totals[kind] += len(hits[kind])
        per_event.append(
            {
                "event_id": e.event_id,
                "category": e.category,
                "category_name": e.category_name,
                "date_start": e.date_start,
                "date_end": e.date_end,
                "affected": {k: len(v) for k, v in hits.items()},
                "details": hits,
            }
        )

    by_category: dict[str, int] = {}
    for e in events:
        c = e.category_name
        by_category[c] = by_category.get(c, 0) + 1

    summary: dict[str, Any] = {
        "n_events": len(events),
        "totals": totals,
        "by_category": by_category,
    }

    return {"summary": summary, "per_event": per_event}
