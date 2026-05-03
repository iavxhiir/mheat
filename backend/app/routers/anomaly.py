"""SST anomaly raster endpoint — PNG or CSV tile for a given date.

Anomaly = ``sst(t) - seasonal_climatology(doy(t))``. Pulls a single-day slice
from the cached SST cube (lazy-fills from CMS on miss) and subtracts the
pre-computed Hobday baseline. Returns ``503`` if the climatology Zarr is
absent. PNGs use diverging RdBu_r; ``fmt=csv`` emits
``latitude,longitude,anomaly_degC``. Both carry ETag + ``Cache-Control:
immutable`` keyed on ``(date, bbox, fmt)``.
"""

from __future__ import annotations

import asyncio
import csv as _csv
import hashlib
import io
import logging
from datetime import UTC, date, datetime
from typing import Literal

import numpy as np
import xarray as xr
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..sst import CMSCredentialsMissingError, SSTCacheMissingError, SSTProvider
from ._caching import json_with_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["anomaly"])

# Anomaly colour-mapping range (°C) and PNG cap.
_VMIN = -5.0
_VMAX = 5.0
_MAX_PIXELS = 512
# Was `public, max-age=86400, immutable` — the `immutable` flag told the
# browser to never re-validate, which trapped users on stale PNGs through
# the entire 2026-05-03 land-mask + bilinear-bleed bugfix series. Now use
# a short max-age + ETag-based revalidation, so the browser sends an
# `If-None-Match` per request and gets a 304 only if our (date, bbox,
# fmt) ETag still matches. If the underlying climatology / colormap /
# upsample changes, the ETag changes and the new PNG is served.
_CACHE_CONTROL = "public, max-age=300, must-revalidate"
_SST_VAR_CANDIDATES = ("analysed_sst", "sst", "thetao")

Fmt = Literal["png", "csv"]


def _etag(iso_date: str, bbox: str | None, fmt: Fmt) -> str:
    """Strong ETag for an (date, bbox, fmt) tuple."""
    h = hashlib.sha1(f"{iso_date}|{bbox or ''}|{fmt}".encode()).hexdigest()[:16]
    return f'"{h}"'


def _pick_sst_da(ds: xr.Dataset) -> xr.DataArray:
    """Pick the SST DataArray and standardize lat/lon dim names."""
    for candidate in _SST_VAR_CANDIDATES:
        if candidate in ds.data_vars:
            da = ds[candidate]
            break
    else:
        raise ValueError("No SST variable in dataset")
    rename: dict[str, str] = {}
    if "lat" in da.dims and "latitude" not in da.dims:
        rename["lat"] = "latitude"
    if "lon" in da.dims and "longitude" not in da.dims:
        rename["lon"] = "longitude"
    return da.rename(rename) if rename else da


# Cached land mask per (h, w) at the upsampled raster resolution. The
# global-land-mask package ships a precomputed 1' GSHHG-derived land grid;
# we sample it at our target lon/lat grid once at first use, then reuse
# the boolean for every PNG render. Drops the "raster paints over land"
# bug at the source: even if the CMS reanalysis has finite SST values for
# coastal lagoons / shallow bays / island shoals, those pixels render
# transparent in the served PNG.
_LAND_MASK_CACHE: dict[tuple[int, int], np.ndarray] = {}


def _land_mask_for_grid(h: int, w: int) -> np.ndarray:
    """Build (and cache) a (h, w) bool land-mask aligned to the cube bbox.

    Uses BBOX from settings (-6,30,36.5,46 by default for the Mediterranean).
    Returns True for *land* (which the renderer turns transparent).

    The mask is **dilated by ~7 km** (one cube-cell radius) so cells whose
    centre is just offshore but whose square footprint laps onto coastal
    land render transparent. Without dilation, the raster's 7 km cells
    visually overlap the basemap's high-resolution coastline by up to
    half a cell along every coastline — what the user reads as "the
    raster bleeds onto land". The dilation costs ~half a cell of raster
    coverage along each coast in exchange for clean coastlines.
    """
    key = (h, w)
    cached = _LAND_MASK_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from global_land_mask import globe
        from ..config import Settings
        bbox = Settings().bbox_tuple  # (lon_min, lat_min, lon_max, lat_max)
        # Use cell-edge extent (matches the frontend MapLibre image corners)
        # so the mask aligns with the painted raster, not the cube cell
        # centres. Half-cell shift = 0.0625° / 2 at the cube grid.
        half = 0.0625 / 2.0
        lons = np.linspace(bbox[0] - half, bbox[2] + half, w)
        lats = np.linspace(bbox[1] - half, bbox[3] + half, h)
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        mask = globe.is_land(lat_grid, lon_grid).astype(bool)
        # Dilate the land mask by ~7 km so coastal raster cells don't
        # visually lap onto the basemap coastline. Compute the dilation
        # radius in PNG pixels: at ~1356×508 over the Med bbox, a pixel
        # is ~3 km, so a 2-3 pixel dilation = ~6-9 km = one cube cell.
        try:
            from scipy.ndimage import binary_dilation
            mask = binary_dilation(mask, iterations=1)  # ~3 km buffer
        except ImportError:
            # Pure numpy fallback — manual 3x3 dilation, 1 iteration.
            m = mask.copy()
            m[1:, :] |= mask[:-1, :]
            m[:-1, :] |= mask[1:, :]
            m[:, 1:] |= mask[:, :-1]
            m[:, :-1] |= mask[:, 1:]
            mask = m
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "global-land-mask unavailable (%s); skipping coastline trim", exc,
        )
        mask = np.zeros((h, w), dtype=bool)
    _LAND_MASK_CACHE[key] = mask
    return mask


def _render_anomaly_png(anomaly_2d: np.ndarray) -> bytes:
    """Encode a 2-D anomaly array (°C) as a north-up RdBu_r PNG.

    The native SST grid is 0.0625° (~7 km), which renders as a visible
    pixel quilt at basin zoom. We bilinear-upsample the FLOAT anomaly
    field 4× *before* applying the divergent RdBu_r colormap — that
    way the colour ramp follows the continuous interpolated value
    instead of being computed at the coarse grid and then stretched
    (which produces blocky colour-band artifacts at sharp transitions).

    A coastline land-mask (Natural Earth via global-land-mask) is applied
    on top of the cube's own NaN mask to trim coastal lagoons, river
    deltas, and island shoals that the CMS reanalysis treats as sea but
    that visually look like land at basin zoom.
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import colormaps
    from PIL import Image

    anom = np.asarray(anomaly_2d, dtype="float32")
    h, w = anom.shape
    step = max(1, int(np.ceil(max(h, w) / _MAX_PIXELS)))
    if step > 1:
        anom = anom[::step, ::step]
        h, w = anom.shape

    # Bilinear upsample the value field 4× — keep NaN as transparent
    # by recording the mask separately and resampling it nearest.
    UPS = 4
    nan_mask = ~np.isfinite(anom)
    # Replace NaN with 0 for the smoothing pass so PIL doesn't propagate
    # them as opaque-looking artifacts.
    safe = np.where(nan_mask, 0.0, anom).astype("float32")
    val_img = Image.fromarray(safe, mode="F").resize(
        (w * UPS, h * UPS), Image.Resampling.BILINEAR,
    )
    mask_img = Image.fromarray((nan_mask * 255).astype("uint8"), mode="L").resize(
        (w * UPS, h * UPS), Image.Resampling.NEAREST,
    )
    smooth = np.asarray(val_img, dtype="float32")
    smooth_mask = np.asarray(mask_img, dtype="uint8") > 0

    # Layer the explicit land mask on top of the cube's NaN mask. Built at
    # the upsampled resolution so it traces the coastline at the same
    # fidelity as the rendered tiles.
    land_mask = _land_mask_for_grid(h * UPS, w * UPS)

    cmap = colormaps.get_cmap("RdBu_r")
    norm = np.clip((smooth - _VMIN) / (_VMAX - _VMIN), 0.0, 1.0)
    rgba = (cmap(norm) * 255).astype(np.uint8)
    rgba[smooth_mask | land_mask] = [0, 0, 0, 0]
    rgba = np.flipud(rgba)  # north-up

    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_anomaly_csv(
    anomaly_2d: np.ndarray,
    latitudes: np.ndarray | None = None,
    longitudes: np.ndarray | None = None,
) -> bytes:
    """Encode a 2-D anomaly array (°C) as ``lat,lon,anomaly_degC`` CSV."""
    anom = np.asarray(anomaly_2d)
    h, w = anom.shape
    lats = latitudes if latitudes is not None else np.arange(h, dtype="float32")
    lons = longitudes if longitudes is not None else np.arange(w, dtype="float32")
    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(["latitude", "longitude", "anomaly_degC"])
    for i in range(h):
        for j in range(w):
            v = anom[i, j]
            writer.writerow([
                f"{float(lats[i]):.6f}", f"{float(lons[j]):.6f}",
                "" if not np.isfinite(v) else f"{float(v):.4f}",
            ])
    return buf.getvalue().encode("utf-8")


def _compute_anomaly(
    provider: SSTProvider, target: date,
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Return ``(anom_2d, lats, lons)``, raising HTTPException(503) if clim missing.

    Routes the climatology-missing case through the canonical error envelope by
    raising rather than returning a raw JSONResponse — keeps every MHEAT 4xx/5xx
    body shaped as ``{"error": {...}}`` with a stable ``code`` slug.
    """
    settings = provider.settings
    clim = provider.load_climatology()
    if clim is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "climatology_missing",
                "detail": "Run scripts/bootstrap_climatology.py first.",
                "climatology_store": str(settings.climatology_store),
            },
        )
    da = _pick_sst_da(provider.load_range(target, target))
    times = (da["time"].values if "time" in da.dims
             else np.array([np.datetime64(target.isoformat())]))
    seas, _ = clim.expand_to_cube(times)
    anom = da.values - seas
    if anom.ndim == 3 and anom.shape[0] == 1:
        anom = anom[0]
    lats = da["latitude"].values if "latitude" in da.coords else None
    lons = da["longitude"].values if "longitude" in da.coords else None
    return anom, lats, lons


def _build_anomaly_payload(
    settings: Settings, provider: SSTProvider, target: date, fmt: Fmt,
) -> tuple[bytes, str]:
    """Return ``(body, media_type)``. ``HTTPException`` propagates on missing clim."""
    anom, lats, lons = _compute_anomaly(provider, target)
    if fmt == "png":
        return _render_anomaly_png(anom), "image/png"
    return _render_anomaly_csv(anom, lats, lons), "text/csv"


# Server-side LRU around the rendered payload — keyed only on (date_iso, fmt)
# because climatology + cube extent change at deploy / boot time, not per
# request. The 4× PIL bilinear upsample we do in `_render_anomaly_png` is
# the slowest step (~700 ms cold per B4 perf bench); caching the rendered
# bytes drops cold-skim P95 from ~960 ms → first-paint cost only. Cleared
# on cube extent change via the lifespan startup hook.
_PAYLOAD_LRU_MAX = 64
_payload_lru: dict[tuple[str, str], tuple[bytes, str]] = {}
_payload_lru_keys: list[tuple[str, str]] = []


def _build_anomaly_payload_cached(
    settings: Settings, provider: SSTProvider, target: date, fmt: Fmt,
) -> tuple[bytes, str]:
    key = (target.isoformat(), str(fmt))
    cached = _payload_lru.get(key)
    if cached is not None:
        return cached
    payload = _build_anomaly_payload(settings, provider, target, fmt)
    _payload_lru[key] = payload
    _payload_lru_keys.append(key)
    if len(_payload_lru_keys) > _PAYLOAD_LRU_MAX:
        old = _payload_lru_keys.pop(0)
        _payload_lru.pop(old, None)
    return payload


def clear_anomaly_cache() -> None:
    """Drop the in-process anomaly payload cache. Call when the cube grows."""
    _payload_lru.clear()
    _payload_lru_keys.clear()


@router.get(
    "/anomaly",
    summary="SST anomaly tile (PNG or CSV) for a given date",
    description=(
        "SST anomaly (obs minus seasonal climatology) for the date. "
        "``fmt=png`` (default) → coloured PNG; ``fmt=csv`` → flat "
        "``latitude,longitude,anomaly_degC`` table. ETag + immutable cache."
    ),
    response_description="PNG (image/png) or CSV (text/csv) with caching headers",
    responses={
        200: {"content": {"image/png": {}, "text/csv": {}}},
        304: {"description": "Not modified (ETag matched)"},
        400: {"description": "Date out of range"},
        503: {
            "description": "Climatology artifact missing or upstream CMS unavailable",
            "content": {
                "application/json": {
                    "example": {
                        "status": "climatology_missing",
                        "detail": "Run scripts/bootstrap_climatology.py first.",
                        "climatology_store": "/data/cache/climatology.zarr",
                    }
                }
            },
        },
    },
)
async def anomaly_png(
    date_: date = Query(..., alias="date", description="YYYY-MM-DD"),
    fmt: Fmt = Query("png", description="Output format: png|csv"),
    bbox: str | None = Query(None, description="Reserved; bbox currently ignored"),
    if_none_match: str | None = Header(
        None, alias="If-None-Match",
        description="Conditional request: send a previous ETag and receive 304 on match.",
    ),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache=Depends(cache_dep),
) -> Response:
    """Return a PNG / CSV of the SST anomaly at the requested date."""
    iso = date_.isoformat()
    etag = _etag(iso, bbox, fmt)
    if if_none_match and if_none_match.strip() == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
        )
    try:
        body, media_type = await asyncio.to_thread(
            _build_anomaly_payload_cached, settings, sst, date_, fmt,
        )
    except CMSCredentialsMissingError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SSTCacheMissingError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(
        content=body, media_type=media_type,
        headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
    )


@router.get(
    "/anomaly/extent",
    summary="Temporal extent covered by the anomaly endpoint",
    response_description="First & last available date plus colour-scale range",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "start": "1991-01-01",
                        "end": "2026-04-25",
                        "vmin_degC": -5.0,
                        "vmax_degC": 5.0,
                    }
                }
            }
        }
    },
)
def anomaly_extent(
    request: Request,
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
) -> Response:
    """Return the date range the anomaly endpoint can serve.

    Reports the cached cube's extent when available; falls back to the
    climatology window or a "last 12 months" window otherwise so the UI
    always has a sensible default.
    """
    today = datetime.now(UTC).date()
    extent = sst.cube_extent()
    if extent is not None:
        cube_start, cube_end = extent
        payload: dict = {
            "start": cube_start.isoformat(),
            "end": cube_end.isoformat(),
            "n_days": (cube_end - cube_start).days + 1,
            "vmin_degC": _VMIN, "vmax_degC": _VMAX,
        }
        return json_with_cache(request, payload, max_age=60)

    clim = sst.load_climatology()
    if clim is not None:
        clim_start = int(clim.attrs.get("clim_start", today.year - 1))
        payload = {
            "start": f"{clim_start}-01-01",
            "end": today.isoformat(),
            "vmin_degC": _VMIN, "vmax_degC": _VMAX,
        }
        return json_with_cache(request, payload, max_age=60)

    logger.warning(
        "/anomaly/extent: cache and climatology both empty, returning "
        "last 12 months ending %s as a placeholder", today.isoformat(),
    )
    payload = {
        "start": today.replace(year=today.year - 1).isoformat(),
        "end": today.isoformat(),
        "vmin_degC": _VMIN, "vmax_degC": _VMAX,
    }
    # Placeholder fallback — short cache so the real cube replaces it asap.
    return json_with_cache(request, payload, max_age=10)
