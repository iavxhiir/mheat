"""STAC endpoints serving a dynamic catalog backed by the on-disk data."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from starlette.responses import Response

from ..cache import CacheStore
from ..climatology import Climatology
from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..sst import SSTProvider
from ..stac import COLLECTION_ID, build_collection, build_items
from ._caching import json_with_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stac", tags=["stac"])

# STAC catalogue rebuilds whenever the SST cube grows by a day → 60 s TTL
# matches what /api/events already advertises.
_STAC_MAX_AGE = 60


def _try_load_climatology(provider: SSTProvider) -> Climatology | None:
    """Load the pre-computed Hobday baseline if the artifact exists, else None.

    Wrapped in try/except so a corrupt or unreadable zarr never breaks the
    catalog endpoint — the climatology Item simply won't appear in that case.
    """
    try:
        return provider.load_climatology()
    except Exception as exc:  # noqa: BLE001
        logger.info("STAC: climatology unavailable (%s)", exc)
        return None


def _collection_with_extent(
    settings: Settings, cache: CacheStore, climatology: Climatology | None,
) -> dict[str, Any]:
    """Build a Collection doc with the proposal's coverage commitment.

    STAC Collection ``extent.temporal`` represents the dataset's overall
    coverage commitment, not what's currently materialised. We pin the
    start to **1987-01-01** (the Med MFC reanalysis floor that the proposal
    commits to) and leave the end ``None`` ("open-ended", per STAC) so the
    rolling NRT/forecast tail isn't truncated to whatever the cache holds
    today. Per-Item extents in :func:`build_items` continue to reflect
    actual cached data and can be queried for the current state.

    Spatial extent is derived from real items when present so the bbox
    snaps to the cube's coords; falls back to the basin envelope otherwise.
    """
    items = build_items(settings, cache, climatology=climatology)
    spatial: list[list[float]] | None = None
    if items:
        bboxes: list[list[float]] = [
            it["bbox"] for it in items if isinstance(it.get("bbox"), list)
        ]
        if bboxes:
            lon_min = min(b[0] for b in bboxes)
            lat_min = min(b[1] for b in bboxes)
            lon_max = max(b[2] for b in bboxes)
            lat_max = max(b[3] for b in bboxes)
            spatial = [[lon_min, lat_min, lon_max, lat_max]]
    # Open-ended interval keyed off the proposal's coverage floor.
    interval = [["1987-01-01T00:00:00Z", None]]
    return build_collection(time_interval=interval, bbox=spatial)


@router.get(
    "",
    summary="STAC Catalog root",
    description=(
        "STAC 1.0.0 Catalog landing page. Entry point for STAC clients "
        "(pystac, QGIS STAC plugin, etc.) — exposes `child` links to every "
        "published Collection plus the standard `root`/`self` self-references."
    ),
    response_description="STAC Catalog object",
)
def catalog_root(
    request: Request,
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return the STAC Catalog root document (STAC 1.0.0 §Catalog)."""
    clim = _try_load_climatology(sst)
    coll = _collection_with_extent(settings, cache, clim)
    payload: dict[str, Any] = {
        "type": "Catalog",
        "stac_version": "1.0.0",
        "id": "mheat-catalog",
        "title": "MHEAT — Mediterranean Marine Heatwaves catalog",
        "description": (
            "Root catalog for MHEAT-derived marine heatwave events, the "
            "cached SST cube, and the Hobday 1991-2020 climatology — all "
            "served as STAC 1.0.0 Collections / Items with ARCO Zarr assets."
        ),
        "conformsTo": [
            "https://api.stacspec.org/v1.0.0/core",
            "https://api.stacspec.org/v1.0.0/collections",
        ],
        "links": [
            {"rel": "self", "href": "/api/stac", "type": "application/json"},
            {"rel": "root", "href": "/api/stac", "type": "application/json"},
            {"rel": "data", "href": "/api/stac/collections", "type": "application/json"},
            {
                "rel": "child",
                "href": f"/api/stac/collections/{COLLECTION_ID}",
                "type": "application/json",
                "title": coll.get("title"),
            },
            {"rel": "service-desc", "href": "/api/openapi.json", "type": "application/vnd.oai.openapi+json;version=3.0"},
            {"rel": "service-doc", "href": "/api/docs", "type": "text/html"},
        ],
    }
    return json_with_cache(request, payload, max_age=_STAC_MAX_AGE)


@router.get(
    "/collections",
    summary="List STAC collections",
    description="STAC Collections endpoint — enumerates every collection MHEAT publishes.",
    response_description="STAC-style JSON with a `collections` array",
)
def collections(
    request: Request,
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return the STAC catalog root listing every published collection."""
    clim = _try_load_climatology(sst)
    payload: dict[str, Any] = {
        "collections": [_collection_with_extent(settings, cache, clim)],
        "links": [{"rel": "self", "href": "/api/stac/collections"}],
    }
    return json_with_cache(request, payload, max_age=_STAC_MAX_AGE)


@router.get(
    "/collections/{collection_id}",
    summary="Get one STAC collection",
    description="Fetch the metadata document for a single STAC collection.",
    response_description="STAC Collection object",
)
def get_collection(
    request: Request,
    collection_id: str = Path(
        ..., description=f"STAC collection id (currently only `{COLLECTION_ID}`)",
    ),
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return the metadata document for a single STAC collection."""
    if collection_id != COLLECTION_ID:
        raise HTTPException(status_code=404, detail="Collection not found")
    clim = _try_load_climatology(sst)
    return json_with_cache(
        request, _collection_with_extent(settings, cache, clim),
        max_age=_STAC_MAX_AGE,
    )


@router.get(
    "/collections/{collection_id}/items",
    summary="List items in a collection",
    description="Returns a STAC ItemCollection (GeoJSON FeatureCollection with STAC properties).",
    response_description="STAC ItemCollection",
)
def list_items(
    request: Request,
    collection_id: str = Path(
        ..., description=f"STAC collection id (currently only `{COLLECTION_ID}`)",
    ),
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return every STAC item in the given collection as an ItemCollection."""
    if collection_id != COLLECTION_ID:
        raise HTTPException(status_code=404, detail="Collection not found")
    clim = _try_load_climatology(sst)
    items = build_items(settings, cache, climatology=clim)
    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": items,
        "links": [{"rel": "self", "href": f"/api/stac/collections/{collection_id}/items"}],
    }
    return json_with_cache(request, payload, max_age=_STAC_MAX_AGE)


@router.get(
    "/collections/{collection_id}/items/{item_id}",
    summary="Get one STAC item",
    description="Fetch a single STAC Item by id.",
    response_description="STAC Item (GeoJSON Feature with STAC properties)",
)
def get_item(
    request: Request,
    collection_id: str = Path(
        ..., description=f"STAC collection id (currently only `{COLLECTION_ID}`)",
    ),
    item_id: str = Path(
        ..., description="STAC item id (e.g. `mheat-sst-cube-mediterranean-2024`)",
    ),
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return a single STAC item by id, or 404 if not found."""
    if collection_id != COLLECTION_ID:
        raise HTTPException(status_code=404, detail="Collection not found")
    clim = _try_load_climatology(sst)
    for it in build_items(settings, cache, climatology=clim):
        if it["id"] == item_id:
            return json_with_cache(request, it, max_age=_STAC_MAX_AGE)
    raise HTTPException(status_code=404, detail="Item not found")
