"""Overlay endpoints: aquaculture / MPA / seagrass GeoJSON."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from starlette.responses import Response

from ..cache import CacheStore
from ..config import Settings
from ..deps import cache_dep, settings_dep
from ..overlays import OverlayProvider, list_overlay_kinds
from ._caching import json_with_cache

router = APIRouter(prefix="/api/overlays", tags=["overlays"])

# Overlays change at most when the upstream WFS refreshes — minutes are fine.
_OVERLAY_LIST_MAX_AGE = 600
_OVERLAY_BODY_MAX_AGE = 300


@router.get(
    "",
    summary="List supported overlay kinds",
    description="Enumerates the sectoral overlays available through `/api/overlays/{kind}`.",
    response_description="List of overlay identifiers",
)
def list_kinds(request: Request) -> Response:
    """Return the list of supported overlay identifiers."""
    return json_with_cache(
        request, {"kinds": list_overlay_kinds()}, max_age=_OVERLAY_LIST_MAX_AGE,
    )


@router.get(
    "/{kind}",
    summary="Fetch an overlay as GeoJSON",
    description=(
        "Returns a GeoJSON FeatureCollection for one of the sectoral overlays "
        "(`aquaculture`, `mpa`, `seagrass`). Geometries are pre-fetched from "
        "EMODnet / EEA WFS and cached locally."
    ),
    response_description="GeoJSON FeatureCollection",
)
def get_overlay(
    request: Request,
    kind: str = Path(
        ..., description="One of `aquaculture`, `mpa`, `seagrass`",
    ),
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
) -> Response:
    """Return the GeoJSON FeatureCollection for a single overlay kind."""
    provider = OverlayProvider(settings=settings, cache=cache)
    try:
        gj = provider.get(kind)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Unknown overlay kind: {kind}") from e
    return json_with_cache(request, gj, max_age=_OVERLAY_BODY_MAX_AGE)
