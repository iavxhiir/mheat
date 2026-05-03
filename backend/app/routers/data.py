"""ARCO data assets — exposes the on-disk Zarr stores at ``/api/data/*``.

EDITO §5 mandates that dataset outputs comply with the ARCO (Analysis-Ready
Cloud-Optimized) format. MHEAT writes two such assets to ``data/cache/``:

* ``sst.zarr`` — the cached Mediterranean SST cube populated by the startup
  prefetch and lazy-fill from Copernicus Marine.
* ``climatology.zarr`` — the pre-computed Hobday seasonal mean +
  90th-percentile threshold built once by ``scripts/bootstrap_climatology.py``.

This router serves both as static byte ranges so a remote
``xarray.open_zarr(http_url, consolidated=True)`` call can discover and pull
chunks on demand. It also publishes a JSON index at ``/api/data`` so STAC
asset HREFs and reviewers can browse what's there.

The mount is namespaced under ``/api/data/`` (not the root ``/data/`` path
the cache uses on disk) so the URL surface stays under the API umbrella —
clients always go through ``/api/*``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse, Response

from ..config import Settings
from ..deps import settings_dep
from ._caching import json_with_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data", tags=["data"])

# Asset index changes when an asset is rebuilt / size grows on disk; a
# 60 s TTL is enough for browsers + reverse proxies without making a
# fresh prefetch invisible.
_DATA_INDEX_MAX_AGE = 60

_ASSETS = ("sst.zarr", "climatology.zarr")
_ZARR_MIME = "application/vnd+zarr"


def _asset_root(settings: Settings, name: str) -> Path:
    """Map a public asset name to its on-disk Zarr root."""
    if name == "sst.zarr":
        return Path(settings.zarr_store)
    if name == "climatology.zarr":
        return Path(settings.climatology_store)
    raise HTTPException(status_code=404, detail=f"Unknown asset: {name}")


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _asset_summary(settings: Settings, name: str) -> dict[str, Any]:
    root = _asset_root(settings, name)
    present = root.exists()
    return {
        "name": name,
        "href": f"/api/data/{name}",
        "type": _ZARR_MIME,
        "roles": ["data"],
        "present": present,
        "size_bytes": _dir_size_bytes(root) if present else 0,
    }


@router.get(
    "",
    summary="Browse ARCO data assets",
    description=(
        "Returns a JSON index of the Zarr stores MHEAT exposes. Each entry "
        "carries an ARCO-compliant ``application/vnd+zarr`` MIME and a "
        "byte size so a reviewer or STAC client can decide whether to pull "
        "the asset."
    ),
)
def list_assets(
    request: Request,
    settings: Settings = Depends(settings_dep),
) -> Response:
    payload: dict[str, Any] = {
        "assets": [_asset_summary(settings, name) for name in _ASSETS],
        "format": "ARCO/Zarr v2 (consolidated metadata)",
        "documentation": "/api/docs",
    }
    return json_with_cache(request, payload, max_age=_DATA_INDEX_MAX_AGE)


@router.get(
    "/{asset}",
    summary="Asset metadata (Zarr root .zmetadata)",
    description=(
        "Convenience redirect to the consolidated Zarr metadata file at "
        "``<asset>/.zmetadata``. ``xarray.open_zarr(<asset_url>, consolidated="
        "True)`` will follow this through to the per-array chunks."
    ),
    responses={
        200: {"content": {"application/json": {}}},
        404: {"description": "Asset is not present on disk"},
    },
)
def asset_root(
    asset: str = PathParam(
        ..., description="Asset id — `sst.zarr` or `climatology.zarr`",
    ),
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    """Serve ``<asset>/.zmetadata`` so consolidated-metadata clients work."""
    root = _asset_root(settings, asset)
    zmeta = root / ".zmetadata"
    if not zmeta.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"{asset} is not present at {root}. "
                "Run scripts/bootstrap_climatology.py to seed it."
            ),
        )
    return FileResponse(zmeta, media_type="application/json")


@router.get(
    "/{asset}/{chunk_path:path}",
    summary="Asset chunk / inner Zarr file",
    description=(
        "Serves a single file inside the requested Zarr store — chunks "
        "(e.g. ``analysed_sst/0.0.0``), per-array metadata "
        "(``analysed_sst/.zarray``), or the consolidated ``.zmetadata``. "
        "The path is sandboxed inside the asset root."
    ),
)
def asset_chunk(
    asset: str = PathParam(
        ..., description="Asset id — `sst.zarr` or `climatology.zarr`",
    ),
    chunk_path: str = PathParam(
        ..., description="Path inside the asset (e.g. `analysed_sst/0.0.0`)",
    ),
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    """Serve any file beneath the asset root (sandboxed against ``..`` traversal)."""
    root = _asset_root(settings, asset).resolve()
    target = (root / chunk_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        # ``..`` segment escaped the asset root — refuse, do not leak filesystem layout.
        raise HTTPException(status_code=400, detail="Invalid chunk path") from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"{asset}/{chunk_path} not found")
    # Zarr chunks are opaque binary; metadata files are JSON. Default to
    # octet-stream and let the client interpret.
    media_type = "application/json" if chunk_path.endswith(
        (".zmetadata", ".zarray", ".zgroup", ".zattrs")
    ) else "application/octet-stream"
    return FileResponse(target, media_type=media_type)


def jsonify_404() -> JSONResponse:  # pragma: no cover — helper for callers
    return JSONResponse(status_code=404, content={"detail": "Not Found"})
