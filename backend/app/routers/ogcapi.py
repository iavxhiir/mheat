"""OGC API - Features 1.0 compliant endpoints.

Exposes MHEAT's detected events and sectoral overlays as collections
compliant with `OGC API - Features Core 1.0 <https://ogcapi.ogc.org/features/>`_.
This lets desktop GIS clients (QGIS, ArcGIS Pro) pull layers directly via
Add WFS / OGC API Features → URL.

Collections:
* ``mhw-events`` — detected & clustered marine heatwave polygons
* ``aquaculture`` — sectoral overlay (points)
* ``mpa``         — Natura 2000 marine protected areas (polygons)
* ``seagrass``    — seagrass habitat polygons

All `items` endpoints accept ``bbox``, ``datetime``, ``limit``, ``offset``.
Responses include `Link` headers (next/prev) for paging.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from ..cache import CacheStore
from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..mhw import cluster_events, events_to_geojson, filter_events
from ..overlays import OverlayProvider
from ..sst import SSTProvider
from ._caching import json_with_cache
from .events import _load_baseline_or_503, _run_detection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ogcapi", tags=["ogcapi"])

# Landing / conformance / collections-catalog change at deploy time only;
# 5-minute TTL cuts repeat hits without making operator edits invisible.
_OGC_STATIC_MAX_AGE = 300

_COLLECTION_METADATA: dict[str, dict[str, Any]] = {
    "mhw-events": {
        "id": "mhw-events",
        "title": "Marine heatwave events (clustered)",
        "description": "Clustered marine heatwave events detected via the Hobday et al. (2016) method.",
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "extent": {
            "spatial": {"bbox": [[-6.0, 30.0, 37.0, 46.0]]},
            "temporal": {"interval": [["2022-01-01T00:00:00Z", "2022-12-31T23:59:59Z"]]},
        },
    },
    "aquaculture": {
        "id": "aquaculture",
        "title": "Aquaculture sites",
        "description": "Mediterranean aquaculture sites (EMODnet Human Activities).",
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "extent": {"spatial": {"bbox": [[-6.0, 30.0, 37.0, 46.0]]}},
    },
    "mpa": {
        "id": "mpa",
        "title": "Marine Protected Areas (Natura 2000)",
        "description": "EEA Natura 2000 marine protected area polygons.",
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "extent": {"spatial": {"bbox": [[-6.0, 30.0, 37.0, 46.0]]}},
    },
    "seagrass": {
        "id": "seagrass",
        "title": "Seagrass habitats",
        "description": "Seagrass habitat polygons (EMODnet Seabed Habitats).",
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "extent": {"spatial": {"bbox": [[-6.0, 30.0, 37.0, 46.0]]}},
    },
}

_CONFORMANCE_CLASSES = [
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
    # Part 3 — Filtering / Queryables. Lets EDITO's QGIS-STAC and
    # generic OGC clients discover which properties they can filter on
    # without requesting the full schema and inferring it.
    "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/queryables",
]


# JSON-Schema fragments describing the queryable properties of each
# collection. Per OGC API — Features Part 3 §6.2 the document is JSON
# Schema 2019-09 with `type: "object"` at the root and one entry per
# filterable property under `properties`.
_QUERYABLES: dict[str, dict[str, Any]] = {
    "mhw-events": {
        "$schema": "https://json-schema.org/draft/2019-09/schema",
        "$id": "/api/ogcapi/collections/mhw-events/queryables",
        "type": "object",
        "title": "Marine heatwave event queryables",
        "properties": {
            "event_id": {"type": "string", "title": "Stable cluster id"},
            "date_start": {"type": "string", "format": "date", "title": "Onset date"},
            "date_end": {"type": "string", "format": "date", "title": "End date"},
            "date_peak": {"type": "string", "format": "date", "title": "Peak intensity date"},
            "duration_days": {"type": "integer", "minimum": 5, "title": "Duration in days"},
            "intensity_max": {"type": "number", "title": "Peak SST anomaly °C"},
            "intensity_mean": {"type": "number", "title": "Mean SST anomaly °C over event"},
            "intensity_cumulative": {"type": "number", "title": "Cumulative °C·day"},
            "category": {"type": "integer", "minimum": 1, "maximum": 5,
                          "title": "Hobday severity category (1=Moderate … 5=V Super-Extreme)"},
            "category_name": {"type": "string", "title": "Human-readable category name"},
            "n_pixels": {"type": "integer", "minimum": 1, "title": "Cluster pixel count"},
        },
    },
    "aquaculture": {
        "$schema": "https://json-schema.org/draft/2019-09/schema",
        "$id": "/api/ogcapi/collections/aquaculture/queryables",
        "type": "object",
        "title": "Aquaculture site queryables (EMODnet finfish layer)",
        "properties": {
            "feature_id": {"type": "string", "title": "Upstream EMODnet feature id"},
        },
    },
    "mpa": {
        "$schema": "https://json-schema.org/draft/2019-09/schema",
        "$id": "/api/ogcapi/collections/mpa/queryables",
        "type": "object",
        "title": "Natura 2000 marine MPA queryables (EEA layer 2)",
        "properties": {
            "SITECODE": {"type": "string", "title": "Natura 2000 site code"},
            "SITENAME": {"type": "string", "title": "Site name"},
            "MS": {"type": "string", "title": "Member state ISO-2 code"},
            "SITETYPE": {"type": "string", "title": "Habitat / Birds / both"},
            "RELEASE_DATE": {"type": "string", "format": "date-time"},
            "Area_ha": {"type": "number", "minimum": 0, "title": "Area in hectares"},
        },
    },
    "seagrass": {
        "$schema": "https://json-schema.org/draft/2019-09/schema",
        "$id": "/api/ogcapi/collections/seagrass/queryables",
        "type": "object",
        "title": "Seagrass habitat queryables (EMODnet Seabed Habitats EOV 2025)",
        "properties": {
            "feature_id": {"type": "string", "title": "Upstream EMODnet feature id"},
        },
    },
}


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """Parse ``lon_min,lat_min,lon_max,lat_max`` into a tuple."""
    if not bbox:
        return None
    try:
        parts = [float(p) for p in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        return (parts[0], parts[1], parts[2], parts[3])
    except ValueError as e:
        raise HTTPException(status_code=400, detail="bbox must be 'lon_min,lat_min,lon_max,lat_max'") from e


def _parse_datetime(dt: str | None) -> tuple[date | None, date | None]:
    """Parse OGC datetime query parameter (RFC 3339 instant or interval)."""
    if not dt:
        return None, None
    try:
        if "/" in dt:
            a, b = dt.split("/", 1)
            start = date.fromisoformat(a[:10]) if a not in ("", "..") else None
            end = date.fromisoformat(b[:10]) if b not in ("", "..") else None
            return start, end
        d = date.fromisoformat(dt[:10])
        return d, d
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {dt}") from e


def _collection_with_links(coll_id: str) -> dict[str, Any]:
    """Return a collection metadata doc with standard OGC API links."""
    meta = dict(_COLLECTION_METADATA[coll_id])
    meta["links"] = [
        {"rel": "self", "href": f"/api/ogcapi/collections/{coll_id}", "type": "application/json"},
        {"rel": "items", "href": f"/api/ogcapi/collections/{coll_id}/items", "type": "application/geo+json"},
        {
            "rel": "http://www.opengis.net/def/rel/ogc/1.0/queryables",
            "href": f"/api/ogcapi/collections/{coll_id}/queryables",
            "type": "application/schema+json",
            "title": "Filterable properties (JSON Schema)",
        },
    ]
    return meta


@router.get(
    "",
    summary="OGC API Features landing page",
    description="Entry point exposing links to conformance, collections, and API docs.",
)
def landing(request: Request) -> Response:
    """Return the OGC API landing document."""
    payload: dict[str, Any] = {
        "title": "MHEAT — OGC API Features",
        "description": (
            "OGC API - Features 1.0 endpoint for MHEAT. "
            "Exposes marine heatwave events and sectoral overlays as GeoJSON feature collections."
        ),
        "links": [
            {"rel": "self", "href": "/api/ogcapi", "type": "application/json", "title": "this document"},
            {"rel": "conformance", "href": "/api/ogcapi/conformance", "type": "application/json"},
            {"rel": "data", "href": "/api/ogcapi/collections", "type": "application/json"},
            {"rel": "service-desc", "href": "/api/openapi.json", "type": "application/vnd.oai.openapi+json;version=3.0"},
            {"rel": "service-doc", "href": "/api/docs", "type": "text/html"},
        ],
    }
    return json_with_cache(request, payload, max_age=_OGC_STATIC_MAX_AGE)


@router.get(
    "/conformance",
    summary="OGC API Features conformance classes",
    description="Lists the OGC API Features conformance classes implemented by this service.",
)
def conformance(request: Request) -> Response:
    """Return the OGC API conformance document."""
    return json_with_cache(
        request, {"conformsTo": _CONFORMANCE_CLASSES}, max_age=_OGC_STATIC_MAX_AGE,
    )


@router.get(
    "/collections",
    summary="List OGC API feature collections",
    description="Enumerates all OGC API feature collections (events, aquaculture, mpa, seagrass).",
)
def list_collections(request: Request) -> Response:
    """Return the collections catalog."""
    colls = [_collection_with_links(cid) for cid in _COLLECTION_METADATA]
    payload: dict[str, Any] = {
        "links": [
            {"rel": "self", "href": "/api/ogcapi/collections", "type": "application/json"},
        ],
        "collections": colls,
    }
    return json_with_cache(request, payload, max_age=_OGC_STATIC_MAX_AGE)


@router.get(
    "/collections/{collection_id}",
    summary="Fetch OGC collection metadata",
    description="Returns metadata (title, description, extent, CRS) for a single collection.",
)
def get_collection(
    request: Request,
    collection_id: str = Path(
        ..., description="One of `mhw-events`, `aquaculture`, `mpa`, `seagrass`",
    ),
) -> Response:
    """Return the metadata document for one collection."""
    if collection_id not in _COLLECTION_METADATA:
        raise HTTPException(status_code=404, detail="Collection not found")
    return json_with_cache(
        request, _collection_with_links(collection_id), max_age=_OGC_STATIC_MAX_AGE,
    )


@router.get(
    "/collections/{collection_id}/queryables",
    summary="OGC API Features Part 3 — queryables",
    description=(
        "JSON Schema document listing the filterable properties for the "
        "collection. Lets clients (QGIS-STAC, EDITO viewers) build property "
        "filters without having to introspect a sample feature."
    ),
)
def queryables(
    request: Request,
    collection_id: str = Path(
        ..., description="One of `mhw-events`, `aquaculture`, `mpa`, `seagrass`",
    ),
) -> Response:
    """Return the queryables JSON Schema for one collection."""
    if collection_id not in _COLLECTION_METADATA:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection_id not in _QUERYABLES:
        raise HTTPException(status_code=404, detail="No queryables document for this collection")
    return json_with_cache(
        request, _QUERYABLES[collection_id], max_age=_OGC_STATIC_MAX_AGE,
    )


def _collect_coords(c: Any, fxs: list[float], fys: list[float]) -> None:
    """Walk a nested GeoJSON coordinate tree, appending lon/lat pairs to ``fxs``/``fys``."""
    if isinstance(c, (int, float)):
        return
    if (
        isinstance(c, list)
        and len(c) >= 2
        and isinstance(c[0], (int, float))
        and isinstance(c[1], (int, float))
    ):
        fxs.append(float(c[0]))
        fys.append(float(c[1]))
        return
    if isinstance(c, list):
        for sub in c:
            _collect_coords(sub, fxs, fys)


def _filter_features_by_bbox(
    features: list[dict[str, Any]],
    bbox: tuple[float, float, float, float] | None,
) -> list[dict[str, Any]]:
    """Filter a list of GeoJSON features by bbox using an AABB test."""
    if not bbox:
        return features
    lon_min, lat_min, lon_max, lat_max = bbox
    out = []
    for f in features:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            continue
        fxs: list[float] = []
        fys: list[float] = []
        _collect_coords(coords, fxs, fys)
        if not fxs:
            continue
        if max(fxs) < lon_min or min(fxs) > lon_max:
            continue
        if max(fys) < lat_min or min(fys) > lat_max:
            continue
        out.append(f)
    return out


def _load_collection_features(
    collection_id: str,
    settings: Settings,
    cache: CacheStore,
    sst: SSTProvider,
    bbox: tuple[float, float, float, float] | None,
    start: date | None,
    end: date | None,
) -> list[dict[str, Any]]:
    """Return the feature list for a given collection, honoring filters."""
    if collection_id == "mhw-events":
        # MHW detection now requires explicit dates and a climatology. Without
        # both we can't return events; rather than 5xx, surface an empty
        # FeatureCollection so OGC API clients see a syntactically-valid
        # response and follow the documented "set datetime= filter to scope"
        # path.
        if start is None or end is None:
            return []
        baseline = _load_baseline_or_503(sst, settings)
        events = _run_detection(settings, sst, cache, start, end, baseline)
        events = filter_events(events, start=start, end=end, bbox=bbox)
        events = cluster_events(events)
        gj = events_to_geojson(events)
        return list(gj.get("features", []))

    if collection_id in ("aquaculture", "mpa", "seagrass"):
        provider = OverlayProvider(settings=settings, cache=cache)
        gj = provider.get(collection_id)
        feats = list(gj.get("features", []))
        return _filter_features_by_bbox(feats, bbox)

    raise HTTPException(status_code=404, detail="Collection not found")


@router.get(
    "/collections/{collection_id}/items",
    summary="List features (paged)",
    description=(
        "Returns a paged GeoJSON FeatureCollection. Supports ``bbox``, ``datetime``, "
        "``limit`` (max 1000) and ``offset``. A ``next`` link header is emitted "
        "when more features are available."
    ),
)
def list_items(
    response: Response,
    collection_id: str = Path(
        ..., description="One of `mhw-events`, `aquaculture`, `mpa`, `seagrass`",
    ),
    bbox: str | None = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
    datetime: str | None = Query(None, description="RFC3339 instant or interval"),
    limit: int = Query(100, ge=1, le=1000, description="Page size (1-1000)"),
    offset: int = Query(0, ge=0, description="Page offset (0-based)"),
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> JSONResponse:
    """Return a paged FeatureCollection for one OGC API collection."""
    if collection_id not in _COLLECTION_METADATA:
        raise HTTPException(status_code=404, detail="Collection not found")

    bbox_tuple = _parse_bbox(bbox)
    dt_start, dt_end = _parse_datetime(datetime)

    features = _load_collection_features(
        collection_id, settings, cache, sst, bbox_tuple, dt_start, dt_end
    )
    total = len(features)

    sliced = features[offset : offset + limit]
    # Ensure every feature has an id so the single-item endpoint can look it up.
    for i, f in enumerate(sliced):
        if "id" not in f or f["id"] is None:
            f["id"] = str(offset + i)

    def _q(new_offset: int) -> str:
        parts = [f"limit={limit}", f"offset={new_offset}"]
        if bbox:
            parts.append(f"bbox={bbox}")
        if datetime:
            parts.append(f"datetime={datetime}")
        return "&".join(parts)

    base = f"/api/ogcapi/collections/{collection_id}/items"
    links: list[dict[str, str]] = [
        {"rel": "self", "href": f"{base}?{_q(offset)}", "type": "application/geo+json"},
    ]
    link_header_parts = [f'<{base}?{_q(offset)}>; rel="self"']
    if offset + limit < total:
        nxt = f"{base}?{_q(offset + limit)}"
        links.append({"rel": "next", "href": nxt, "type": "application/geo+json"})
        link_header_parts.append(f'<{nxt}>; rel="next"')
    if offset > 0:
        prev_offset = max(0, offset - limit)
        prv = f"{base}?{_q(prev_offset)}"
        links.append({"rel": "prev", "href": prv, "type": "application/geo+json"})
        link_header_parts.append(f'<{prv}>; rel="prev"')

    body = {
        "type": "FeatureCollection",
        "features": sliced,
        "numberMatched": total,
        "numberReturned": len(sliced),
        "links": links,
        "timeStamp": None,
    }
    headers = {
        "Link": ", ".join(link_header_parts),
        "Content-Type": "application/geo+json",
    }
    return JSONResponse(body, headers=headers)


@router.get(
    "/collections/{collection_id}/items/{feature_id}",
    summary="Fetch a single feature",
    description="Retrieve one feature from a collection by its ``id``.",
)
def get_item(
    collection_id: str = Path(
        ..., description="One of `mhw-events`, `aquaculture`, `mpa`, `seagrass`",
    ),
    feature_id: str = Path(
        ..., description="Feature id within the collection (string)",
    ),
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> dict[str, Any]:
    """Return a single feature from the given collection."""
    if collection_id not in _COLLECTION_METADATA:
        raise HTTPException(status_code=404, detail="Collection not found")
    features = _load_collection_features(collection_id, settings, cache, sst, None, None, None)
    for i, f in enumerate(features):
        fid = f.get("id")
        if fid is None:
            fid = str(i)
            f["id"] = fid
        if str(fid) == str(feature_id):
            return f
    raise HTTPException(status_code=404, detail="Feature not found")
