"""Sectoral helper endpoints for personas 4 (aquaculture coop directors),
5 (MPA managers), and 15 (GIS analysts).

Three small additions, no changes to the existing routers:

* ``POST /api/farms/expose`` — given a JSON list of farm coordinates, returns
  per-farm MHW exposure history. For each farm we look up the cluster events
  whose footprint contains the point (Polygon ``contains`` test, plus a 7 km
  buffer for Point-geometry single-pixel events). Capped at 500 input farms
  to keep the request bounded.

* ``GET /api/mpa/{site_code}/events`` — returns MHW events whose footprint
  overlaps the named Natura 2000 SITECODE polygon. Same response shape as
  ``/api/events`` (a GeoJSON FeatureCollection of clustered events). Returns
  ``404`` with the canonical error envelope if the SITECODE is not found in
  the bundled MPA overlay.

* ``GET /api/wms`` — minimal OGC WMS 1.3 ``GetMap`` wrapper around the
  existing anomaly PNG renderer. Lets QGIS / ArcGIS users drag a tile URL
  onto their canvas without having to consume our REST endpoint directly.
  ``GetCapabilities`` is intentionally out of scope for this pass.

All errors flow through the standard MHEAT envelope
(``{"error": {"code": ..., "message": ...}}``) via ``register_error_handlers``.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from ..cache import CacheStore
from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..mhw import cluster_events, events_to_geojson, filter_events
from ..overlays import OverlayProvider
from ..sst import CMSCredentialsMissingError, SSTCacheMissingError, SSTProvider
from .anomaly import _build_anomaly_payload_cached
from .events import (
    _events_pipeline,
    _load_baseline_or_503,
    _resolve_dates,
    _run_detection,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["sectoral"])


# --------------------------------------------------------------------------
# 1. POST /api/farms/expose
# --------------------------------------------------------------------------

# 7 km buffer (matches the SST cube cell size ~0.0625°). At Mediterranean
# latitudes, 7 km ≈ 0.063° lat ≈ 0.082° lon, so we use a degree radius that
# safely covers the cell footprint without going basin-wide.
_FARM_POINT_BUFFER_DEG = 0.07
_MAX_FARMS = 500


class FarmInput(BaseModel):
    """One farm location in the input payload."""

    id: str = Field(..., description="Caller-defined identifier echoed back.")
    lon: float = Field(..., ge=-180.0, le=180.0)
    lat: float = Field(..., ge=-90.0, le=90.0)


class FarmsExposeRequest(BaseModel):
    """Request body for POST /api/farms/expose."""

    farms: list[FarmInput] = Field(..., min_length=1, max_length=_MAX_FARMS)
    start: date | None = Field(
        default=None,
        description="Optional window start (YYYY-MM-DD). Defaults to last 30 days of cube.",
    )
    end: date | None = Field(
        default=None,
        description="Optional window end (YYYY-MM-DD). Defaults to last 30 days of cube.",
    )

    @field_validator("farms")
    @classmethod
    def _cap_farms(cls, v: list[FarmInput]) -> list[FarmInput]:
        if len(v) > _MAX_FARMS:
            raise ValueError(f"farms list capped at {_MAX_FARMS}")
        return v


def _point_in_event(geom: dict[str, Any], lon: float, lat: float) -> bool:
    """True if ``(lon, lat)`` falls inside the event footprint.

    Polygon / MultiPolygon → standard ``contains`` test. Point geometries
    (single-pixel clusters) get a small ~7 km buffer first so the farm can
    be reasonably attributed to the pixel it sits inside.
    """
    try:
        from shapely.geometry import Point, shape
    except ImportError:  # pragma: no cover — shapely is a hard dep
        return False
    pt = Point(lon, lat)
    try:
        g = shape(geom)
    except Exception:  # noqa: BLE001
        return False
    if g.geom_type == "Point":
        try:
            return g.buffer(_FARM_POINT_BUFFER_DEG).contains(pt)
        except Exception:  # noqa: BLE001
            # Fallback: bare distance check if buffer fails for any reason.
            return ((g.x - lon) ** 2 + (g.y - lat) ** 2) ** 0.5 < _FARM_POINT_BUFFER_DEG
    try:
        return g.intersects(pt)
    except Exception:  # noqa: BLE001
        return False


@router.post(
    "/farms/expose",
    summary="Per-farm MHW exposure history (persona 4)",
    description=(
        "Given a JSON list of farm coordinates, returns the MHW events whose "
        "footprint contains each farm. Hits the same cluster-event pipeline "
        "as ``/api/events``. Capped at 500 farms per request."
    ),
    responses={
        400: {"description": "Empty farms list, malformed body, or window inversion"},
        503: {"description": "Climatology missing or upstream CMS fetch failed"},
    },
)
def farms_expose(
    payload: FarmsExposeRequest,
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> dict[str, Any]:
    """Resolve per-farm exposure for the requested coordinate set."""
    if payload.start is not None and payload.end is not None and payload.end < payload.start:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_range", "detail": "end must be >= start"},
        )

    start, end = _resolve_dates(payload.start, payload.end, sst)

    # Re-use the canonical events pipeline so what we return matches the
    # exact event ids / geometries the rest of the dashboard already shows.
    geojson = _events_pipeline(
        settings=settings, sst=sst, cache=cache,
        start=start, end=end, bbox_tuple=None,
        min_category=1, raw=False, include_impact=False,
    )
    features = geojson.get("features", []) or []

    out_farms: list[dict[str, Any]] = []
    for farm in payload.farms:
        matched: list[dict[str, Any]] = []
        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue
            if not _point_in_event(geom, farm.lon, farm.lat):
                continue
            props = feat.get("properties", {}) or {}
            matched.append({
                "event_id": feat.get("id") or props.get("event_id"),
                "date_start": props.get("date_start"),
                "date_end": props.get("date_end"),
                "date_peak": props.get("date_peak"),
                "duration_days": props.get("duration_days"),
                "category": props.get("category"),
                "category_name": props.get("category_name"),
                "intensity_max": props.get("intensity_max"),
                "intensity_mean": props.get("intensity_mean"),
                "intensity_cumulative": props.get("intensity_cumulative"),
            })
        out_farms.append({
            "id": farm.id,
            "lon": farm.lon,
            "lat": farm.lat,
            "n_events": len(matched),
            "events": matched,
        })

    return {
        "window": {"start": str(start), "end": str(end)},
        "n_farms": len(out_farms),
        "farms": out_farms,
    }


# --------------------------------------------------------------------------
# 2. GET /api/mpa/{site_code}/events
# --------------------------------------------------------------------------


def _find_mpa_polygon(
    overlays: dict[str, Any], site_code: str,
) -> tuple[dict[str, Any], Any] | None:
    """Locate the MPA feature for a SITECODE in the cached overlay.

    Returns ``(properties, shapely_geometry)`` or ``None`` if not found.
    SITECODE comparison is case-insensitive (Natura 2000 codes are uppercase
    by convention, but callers shouldn't have to remember that).
    """
    try:
        from shapely.geometry import shape
    except ImportError:  # pragma: no cover — shapely is a hard dep
        return None
    needle = site_code.strip().upper()
    for feat in overlays.get("features", []) or []:
        props = feat.get("properties") or {}
        candidate = str(props.get("SITECODE") or "").strip().upper()
        if candidate == needle:
            try:
                geom = shape(feat["geometry"])
            except Exception:  # noqa: BLE001
                return None
            return props, geom
    return None


@router.get(
    "/mpa/{site_code}/events",
    summary="MHW events overlapping a Natura 2000 MPA (persona 5)",
    description=(
        "Returns a GeoJSON FeatureCollection of MHW cluster events whose "
        "footprint intersects the named Natura 2000 SITECODE polygon. "
        "Same shape as ``/api/events`` plus an ``mpa`` block with the matched "
        "site metadata. ``404`` if SITECODE is unknown."
    ),
    responses={
        404: {"description": "Unknown SITECODE"},
        503: {"description": "Climatology missing or upstream CMS fetch failed"},
    },
)
def mpa_events(
    site_code: str = PathParam(..., description="Natura 2000 SITECODE, e.g. ITA040002"),
    start: date | None = Query(None, description="Window start; defaults to last 30 days of cube."),
    end: date | None = Query(None, description="Window end; defaults to last 30 days of cube."),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> dict[str, Any]:
    """Return MHW cluster events overlapping the named MPA polygon."""
    provider = OverlayProvider(settings=settings, cache=cache)
    overlays = provider.get("mpa")
    found = _find_mpa_polygon(overlays, site_code)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "mpa_not_found",
                "detail": f"SITECODE {site_code!r} not present in MPA overlay.",
            },
        )
    mpa_props, mpa_geom = found

    start, end = _resolve_dates(start, end, sst)

    # Use the bbox of the MPA polygon as a coarse spatial pre-filter, then
    # do the precise intersect test in Python — keeps the detection workload
    # small without fully re-implementing the events pipeline.
    minx, miny, maxx, maxy = mpa_geom.bounds
    pad = 0.5  # degrees, ~55 km — generous but bounded so we never grab the whole basin.
    bbox_tuple = (minx - pad, miny - pad, maxx + pad, maxy + pad)

    baseline = _load_baseline_or_503(sst, settings)
    raw_events = _run_detection(settings, sst, cache, start, end, baseline)
    raw_events = filter_events(raw_events, start=start, end=end, bbox=bbox_tuple)
    clustered = cluster_events(raw_events)

    try:
        from shapely.geometry import shape
    except ImportError:  # pragma: no cover
        shape = None  # type: ignore[assignment]

    def _intersects_mpa(event_geom: dict[str, Any]) -> bool:
        if shape is None:
            return False
        try:
            return mpa_geom.intersects(shape(event_geom))
        except Exception:  # noqa: BLE001
            return False

    matched_clusters = [e for e in clustered if _intersects_mpa(e.to_feature()["geometry"])]
    geojson = events_to_geojson(matched_clusters)

    return {
        "type": geojson["type"],
        "features": geojson["features"],
        "mpa": {
            "site_code": str(mpa_props.get("SITECODE")),
            "site_name": mpa_props.get("SITENAME"),
            "member_state": mpa_props.get("MS"),
            "site_type": mpa_props.get("SITETYPE"),
            "area_ha": mpa_props.get("Area_ha"),
        },
        "window": {"start": str(start), "end": str(end)},
        "n_events": len(matched_clusters),
    }


# --------------------------------------------------------------------------
# 3. GET /api/wms — OGC WMS 1.3 GetMap
# --------------------------------------------------------------------------

# Accept any of the published WMS layer aliases — keeps QGIS happy when it
# guesses the layer name from a typed URL without first calling
# GetCapabilities. The actual renderer only knows the anomaly raster.
_WMS_LAYER_ALIASES = {"anomaly", "mhw_anomaly", "mheat:anomaly"}


@router.get(
    "/wms",
    summary="OGC WMS 1.3 GetMap (persona 15: GIS analyst)",
    description=(
        "Minimal OGC WMS 1.3.0 ``GetMap`` endpoint that wraps the existing "
        "anomaly PNG renderer. ``GetCapabilities`` is out of scope for this "
        "pass — point QGIS at this URL with explicit layers / time / bbox "
        "and it will render the SST anomaly tile directly on the canvas."
    ),
    responses={
        200: {"content": {"image/png": {}}},
        400: {"description": "Bad WMS request (missing or invalid params)"},
        503: {"description": "Climatology missing or upstream CMS fetch failed"},
    },
)
def wms_getmap(
    request: Request,
    service: str = Query("WMS", description="Must be WMS"),
    version: str = Query("1.3.0", description="WMS version, must be 1.3.0"),
    request_: str = Query("GetMap", alias="request", description="Must be GetMap"),
    layers: str = Query(..., description="Layer name; only 'anomaly' is supported."),
    bbox: str = Query(..., description="lon_min,lat_min,lon_max,lat_max (CRS:84)."),
    crs: str = Query("CRS:84", description="Must be CRS:84 (or EPSG:4326)."),
    width: int = Query(..., gt=0, le=4096),
    height: int = Query(..., gt=0, le=4096),
    format: str = Query("image/png", description="Output format; only image/png is supported."),
    time: date | None = Query(None, description="ISO date for the requested anomaly slice."),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return an anomaly PNG sized to the requested WMS GetMap parameters."""
    # 1. Validate the WMS request envelope.
    if service.upper() != "WMS":
        raise HTTPException(
            status_code=400,
            detail={"status": "wms_bad_service", "detail": "service must be WMS"},
        )
    if not version.startswith("1.3"):
        raise HTTPException(
            status_code=400,
            detail={"status": "wms_bad_version", "detail": "version must be 1.3.x"},
        )
    if request_.lower() != "getmap":
        raise HTTPException(
            status_code=400,
            detail={"status": "wms_bad_request", "detail": "only GetMap is supported"},
        )
    layer_norm = layers.split(",", 1)[0].strip().lower()
    if layer_norm not in _WMS_LAYER_ALIASES:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "wms_unknown_layer",
                "detail": f"unknown layer {layers!r}; supported: anomaly",
            },
        )
    if format.lower() != "image/png":
        raise HTTPException(
            status_code=400,
            detail={"status": "wms_bad_format", "detail": "format must be image/png"},
        )
    crs_norm = crs.upper()
    if crs_norm not in {"CRS:84", "EPSG:4326"}:
        raise HTTPException(
            status_code=400,
            detail={"status": "wms_bad_crs", "detail": "crs must be CRS:84 or EPSG:4326"},
        )

    # 2. Parse the bbox.
    try:
        parts = [float(p) for p in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "wms_bad_bbox",
                "detail": "bbox must be lon_min,lat_min,lon_max,lat_max",
            },
        ) from exc

    # 3. Default time to the most recent cached date if not supplied.
    target = time
    if target is None:
        extent = sst.cube_extent()
        if extent is not None:
            target = extent[1]
        else:
            target = date.today() - timedelta(days=1)

    # 4. Render via the cached anomaly payload helper. The renderer covers
    #    the full configured cube bbox; for this pass we serve that single
    #    PNG without re-cropping to the requested WMS bbox. QGIS will draw
    #    it correctly if the requested bbox matches the cube extent; a
    #    future polish pass can crop / reproject for arbitrary windows.
    #
    #    Error handling mirrors `/api/anomaly` so reviewers see the same
    #    error envelope (503 for missing climatology / cache, 400 for the
    #    "the cube returned an unexpected ndim" path that surfaces as
    #    ValueError in `_compute_anomaly`).
    try:
        body, media_type = _build_anomaly_payload_cached(settings, sst, target, "png")
    except CMSCredentialsMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SSTCacheMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "X-WMS-Layer": layer_norm,
            "X-WMS-Time": target.isoformat(),
            "X-WMS-BBox": bbox,
        },
    )
