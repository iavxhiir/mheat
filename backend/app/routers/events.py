"""Events endpoint: detected MHW events as GeoJSON.

Live cache-backed contract:

* ``/api/events[.csv|.parquet]`` and ``/api/events/{event_id}`` require
  ``start`` and ``end`` query params (400 ``dates_required`` if missing) and
  a pre-computed climatology artifact (503 ``climatology_missing`` otherwise).
* ``/api/events/{event_id}/series`` defaults its window to the parent event's
  date range padded ±30 days when ``start``/``end`` are omitted, then uses
  the climatology baseline to render the Hobday reference lines.

All requests are served from the local SST cache; uncached date ranges are
lazy-filled from Copernicus Marine on first hit (503 ``cms_unavailable`` if
credentials are missing or the upstream call fails).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..cache import CacheStore
from ..climatology import Climatology
from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..mhw import MhwEvent, cluster_events, detect_cube, events_to_geojson, filter_events
from ..sst import CMSCredentialsMissingError, SSTCacheMissingError, SSTProvider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])

_EVENT_CACHE: dict[str, list[Any]] = {}
_RESPONSE_CACHE: dict[tuple[Any, ...], tuple[str, bytes, float]] = {}


def _cache_ttl_seconds() -> int:
    """Read EVENTS_CACHE_TTL_SECONDS at call-time so tests can monkey-patch."""
    try:
        return max(0, int(os.environ.get("EVENTS_CACHE_TTL_SECONDS", "300")))
    except ValueError:
        return 300


def _events_cache_key(
    *, bbox: str | None, start: date | None, end: date | None,
    min_category: int, raw: bool, include_impact: bool,
    clim_signature: str = "",
) -> tuple[Any, ...]:
    return ("events-v2", bbox or "", str(start or ""),
            str(end or ""), int(min_category), bool(raw), bool(include_impact),
            clim_signature)


def _etag_for(key: tuple[Any, ...], payload: bytes) -> str:
    """Strong ETag computed over the serialised body. Quoted per RFC 7232."""
    h = hashlib.sha256()
    for part in key:
        h.update(str(part).encode("utf-8"))
        h.update(b"|")
    h.update(payload)
    return f'"{h.hexdigest()[:32]}"'


def _cached_response(
    *,
    key: tuple[Any, ...],
    request: Request,
    compute_payload: Callable[[], dict[str, Any]],
) -> Response:
    """Look up or build a cached JSON response with ETag + Cache-Control."""
    now = time.monotonic()
    ttl = _cache_ttl_seconds()
    entry = _RESPONSE_CACHE.get(key)
    if entry is not None and (ttl == 0 or now - entry[2] <= ttl):
        etag, body, _ = entry
    else:
        payload = compute_payload()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        etag = _etag_for(key, body)
        _RESPONSE_CACHE[key] = (etag, body, now)

    if_none_match = request.headers.get("if-none-match")
    cache_control = f"public, max-age={min(ttl, 60)}" if ttl > 0 else "no-store"
    if if_none_match and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return Response(
        content=body,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": cache_control},
    )


def clear_response_cache() -> None:
    """Invalidate the HTTP response cache — hook for tests and admin tasks."""
    _RESPONSE_CACHE.clear()


def clear_event_cache() -> None:
    """Invalidate the per-(start,end) detection cache — hook for tests."""
    _EVENT_CACHE.clear()


class EventProperties(BaseModel):
    event_id: str
    date_start: str
    date_end: str
    date_peak: str
    duration_days: int
    intensity_max: float
    intensity_mean: float
    intensity_cumulative: float
    category: int
    category_name: str
    n_pixels: int = 1
    centroid: list[float]
    impact: dict[str, Any] | None = None


class EventFeature(BaseModel):
    type: str = Field(default="Feature")
    id: str
    geometry: dict[str, Any]
    properties: EventProperties

class EventCollection(BaseModel):
    type: str = Field(default="FeatureCollection")
    features: list[EventFeature]


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        parts = [float(p) for p in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        return (parts[0], parts[1], parts[2], parts[3])
    except ValueError as e:
        raise HTTPException(status_code=400, detail="bbox must be 'lon_min,lat_min,lon_max,lat_max'") from e


def _require_dates(start: date | None, end: date | None) -> None:
    """Reject requests that omit ``start``/``end``."""
    if start is not None and end is not None:
        return
    raise HTTPException(
        status_code=400,
        detail={
            "status": "dates_required",
            "detail": "start and end query params are required",
        },
    )


def _resolve_dates(
    start: date | None, end: date | None, sst: SSTProvider,
) -> tuple[date, date]:
    """Return ``(start, end)`` — defaulting both to the most recent 30 days
    of cached SST when neither is supplied. Raises 400 only when one of the
    two is provided without the other (ambiguous open-ended request).

    Reviewers and CLI users can hit ``/api/events.csv`` with no params and
    get a meaningful download instead of a ``400 dates_required`` envelope;
    full-control callers still pin both ends explicitly.
    """
    if start is not None and end is not None:
        return start, end
    if start is None and end is None:
        try:
            ds = sst.load()
            times = ds["time"].values
            if times.size == 0:
                raise ValueError("empty time axis")
            cube_end = date.fromisoformat(str(times[-1])[:10])
            cube_start = date.fromisoformat(str(times[0])[:10])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "sst_cache_missing",
                    "detail": (
                        "Cannot infer default date window — SST cache is "
                        "absent. Either bootstrap it or supply explicit "
                        "start+end query params."
                    ),
                    "underlying": str(exc),
                },
            ) from exc
        # 30-day window ending at the cube tail, clamped to cube head.
        defaulted_start = max(cube_start, cube_end - timedelta(days=30))
        return defaulted_start, cube_end
    # Asymmetric — only one of the two is set; that's a true client error.
    raise HTTPException(
        status_code=400,
        detail={
            "status": "dates_required",
            "detail": (
                "Provide both `start` and `end`, or neither (defaults to the "
                "most recent 30 days of cached SST)."
            ),
        },
    )


def _load_baseline_or_503(sst: SSTProvider, settings: Settings) -> Climatology:
    """Return the climatology, or raise 503 if the artifact is absent."""
    baseline = sst.load_climatology()
    if baseline is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "climatology_missing",
                "detail": "Run scripts/bootstrap_climatology.py",
                "climatology_store": str(settings.climatology_store),
            },
        )
    return baseline


def _baseline_signature(baseline: Climatology) -> str:
    """Stable string summary of a climatology, for cache-key invalidation."""
    return f"{baseline.attrs.get('clim_start', '?')}-{baseline.attrs.get('clim_end', '?')}"


def _select_sst_var(ds: Any) -> str:
    """Return the first SST-like variable name in ``ds.data_vars``."""
    for c in ("analysed_sst", "sst", "thetao"):
        if c in ds.data_vars:
            return c
    raise HTTPException(status_code=500, detail=f"No SST variable found in dataset: {list(ds.data_vars)}")


def _normalize_latlon(da: Any) -> Any:
    """Rename ``lat``/``lon`` to ``latitude``/``longitude`` if needed."""
    rename = {s: d for s, d in (("lat", "latitude"), ("lon", "longitude"))
              if s in da.dims and d not in da.dims}
    return da.rename(rename) if rename else da


def _load_sst_or_503(
    sst: SSTProvider, start: date, end: date,
) -> Any:
    """Wrap ``sst.load_range`` so cache/CMS failures surface as 503s."""
    try:
        return sst.load_range(start, end)
    except CMSCredentialsMissingError as e:
        raise HTTPException(
            status_code=503,
            detail={"status": "cms_credentials_missing", "detail": str(e)},
        ) from e
    except SSTCacheMissingError as e:
        raise HTTPException(
            status_code=503,
            detail={"status": "sst_cache_missing", "detail": str(e)},
        ) from e
    except Exception as e:  # noqa: BLE001 — upstream CMS/network failure
        logger.warning("CMS fetch failed for %s..%s: %s", start, end, e)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "cms_unavailable",
                "detail": f"Upstream CMS fetch failed: {e}",
            },
        ) from e


def _run_detection(
    settings: Settings, sst: SSTProvider, cache: CacheStore,
    start: date, end: date, baseline: Climatology,
) -> list[Any]:
    """Run or re-use MHW detection for the requested window.

    The cache key folds in the baseline's clim_start/clim_end so swapping the
    artifact invalidates stale results.
    """
    sig = _baseline_signature(baseline)
    key = f"events_{start}_{end}_{sig}"
    if key in _EVENT_CACHE:
        return _EVENT_CACHE[key]

    ds = _load_sst_or_503(sst, start, end)
    sst_da = _normalize_latlon(ds[_select_sst_var(ds)])
    events = detect_cube(sst_da, clim_period=(settings.clim_start, settings.clim_end), baseline=baseline)
    _EVENT_CACHE[key] = events
    return events


def _events_pipeline(
    *,
    settings: Settings,
    sst: SSTProvider,
    cache: CacheStore,
    start: date,
    end: date,
    bbox_tuple: tuple[float, float, float, float] | None,
    min_category: int,
    raw: bool,
    include_impact: bool,
) -> dict[str, Any]:
    """Shared detect → filter → cluster → GeoJSON pipeline."""
    baseline = _load_baseline_or_503(sst, settings)
    events = _run_detection(settings, sst, cache, start, end, baseline)
    events = filter_events(events, start=start, end=end, bbox=bbox_tuple)
    events = [e for e in events if e.category >= min_category]
    if not raw:
        events = cluster_events(events)

    geojson = events_to_geojson(events)

    if include_impact and events:
        try:
            from ..impact import attach_impact_properties
            from ..overlays import OverlayProvider
            provider = OverlayProvider(settings=settings, cache=cache)
            overlays = {k: provider.get(k) for k in ("aquaculture", "mpa", "seagrass")}
            attach_impact_properties(geojson, events, overlays)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Impact attach failed: %s", exc)

    return geojson


def _find_event_by_id(
    settings: Settings, sst: SSTProvider, cache: CacheStore,
    event_id: str, start: date, end: date,
) -> MhwEvent | None:
    """Best-effort lookup: scan the detection cache (re-running if needed).

    ``event_id`` may be a per-pixel id (``mhw-NNNNNN``) or a cluster id
    (``mhw-cluster-NNNN``). For cluster ids we re-cluster on the fly so the
    lookup matches what the GeoJSON endpoint emits.
    """
    baseline = _load_baseline_or_503(sst, settings)
    raw_events = _run_detection(settings, sst, cache, start, end, baseline)
    for ev in raw_events:
        if ev.event_id == event_id:
            return ev
    if event_id.startswith("mhw-cluster-"):
        for ev in cluster_events(list(raw_events)):
            if ev.event_id == event_id:
                return ev
    return None


def _clim_signature(sst: SSTProvider) -> str:
    """Return the climatology signature, or empty string if absent."""
    clim = sst.load_climatology()
    return _baseline_signature(clim) if clim is not None else ""


@router.get(
    "/events",
    response_model=EventCollection,
    summary="Detected MHW events as GeoJSON FeatureCollection",
    description="Returns space-time clustered MHW events (default) or raw per-pixel events (``raw=true``). Carries a strong ``ETag`` and ``Cache-Control: public, max-age=60``. TTL via ``EVENTS_CACHE_TTL_SECONDS`` (default 300 s).",
    responses={
        400: {
            "description": "Missing required date params",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "status": "dates_required",
                            "detail": "start and end query params are required",
                        }
                    }
                }
            },
        },
        503: {
            "description": "Climatology artifact missing or upstream CMS fetch failed",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "status": "climatology_missing",
                            "detail": "Run scripts/bootstrap_climatology.py",
                            "climatology_store": "/data/cache/climatology.zarr",
                        }
                    }
                }
            },
        },
    },
)
def list_events(
    request: Request,
    bbox: str | None = Query(
        None, description="lon_min,lat_min,lon_max,lat_max",
        openapi_examples={
            "adriatic": {"summary": "Adriatic Sea", "value": "12,40,20,46"},
            "alboran": {"summary": "Alboran Sea (Western Med)", "value": "-6,34,0,37"},
            "mediterranean": {"summary": "Whole Mediterranean + Adriatic", "value": "-6,30,37,46"},
        },
    ),
    start: date | None = Query(None, description="YYYY-MM-DD (required)"),
    end: date | None = Query(None, description="YYYY-MM-DD (required)"),
    min_category: int = Query(
        1, ge=1, le=5, description="Hobday 2018 category filter (I=1…V=5)",
        openapi_examples={
            "all": {"summary": "All events (I+)", "value": 1},
            "severe_plus": {"summary": "Severe or stronger (III+)", "value": 3},
            "extreme_only": {"summary": "Extreme + Super-Extreme (IV+)", "value": 4},
        },
    ),
    raw: bool = Query(False, description="Return raw per-pixel events instead of clusters"),
    include_impact: bool = Query(True, description="Attach per-event impact metrics"),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> Response:
    """Return all MHW events intersecting the optional spatial/temporal filter.

    With no `start`/`end` provided, defaults to the most recent 30 days of
    cached SST so a reviewer can hit the endpoint without first having to
    introspect the cube extent.
    """
    bbox_tuple = _parse_bbox(bbox)  # validate early so a bad bbox 400s before cache work
    start, end = _resolve_dates(start, end, sst)

    def _build() -> dict[str, Any]:
        return _events_pipeline(
            settings=settings, sst=sst, cache=cache,
            start=start, end=end, bbox_tuple=bbox_tuple,
            min_category=min_category, raw=raw, include_impact=include_impact,
        )

    key = _events_cache_key(
        bbox=bbox, start=start, end=end, min_category=min_category,
        raw=raw, include_impact=include_impact,
        clim_signature=_clim_signature(sst),
    )
    return _cached_response(key=key, request=request, compute_payload=_build)


_CSV_COLUMNS = [
    "event_id", "date_start", "date_end", "date_peak", "duration_days",
    "intensity_max", "intensity_mean", "intensity_cumulative",
    "category", "category_name", "n_pixels", "centroid_lon", "centroid_lat",
    "n_aquaculture_sites", "mpa_area_km2", "seagrass_area_km2",
]


def _csv_rows(geojson: dict[str, Any]) -> Iterator[str]:
    """Yield CSV lines for streaming."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    yield buf.getvalue()
    buf.seek(0)
    buf.truncate(0)

    for feat in geojson.get("features", []):
        p = feat.get("properties", {}) or {}
        centroid = p.get("centroid") or [None, None]
        impact = p.get("impact") or {}
        writer.writerow([
            p.get("event_id"), p.get("date_start"), p.get("date_end"),
            p.get("date_peak"), p.get("duration_days"), p.get("intensity_max"),
            p.get("intensity_mean"), p.get("intensity_cumulative"),
            p.get("category"), p.get("category_name"), p.get("n_pixels"),
            centroid[0] if len(centroid) > 0 else None,
            centroid[1] if len(centroid) > 1 else None,
            impact.get("n_aquaculture_sites", 0),
            impact.get("mpa_area_km2", 0.0),
            impact.get("seagrass_area_km2", 0.0),
        ])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


@router.get(
    "/events.csv",
    summary="Detected MHW events as a CSV download",
    description="Streams the same event catalog as ``/api/events`` formatted as CSV.",
    response_description="CSV file (text/csv) with one row per event",
    responses={200: {"content": {"text/csv": {}}}},
)
def list_events_csv(
    bbox: str | None = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
    start: date | None = Query(None, description="YYYY-MM-DD (required)"),
    end: date | None = Query(None, description="YYYY-MM-DD (required)"),
    min_category: int = Query(
        1, ge=1, le=5, description="Hobday 2018 category filter (I=1…V=5)",
    ),
    raw: bool = Query(False, description="Return raw per-pixel events instead of clusters"),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> StreamingResponse:
    """CSV flavour of :func:`list_events` for offline analysis / spreadsheets.

    With no `start`/`end` query, defaults to the most recent 30 days of cached
    SST so a reviewer can `curl /api/events.csv -o events.csv` without first
    having to introspect the cube extent.
    """
    bbox_tuple = _parse_bbox(bbox)
    start, end = _resolve_dates(start, end, sst)
    geojson = _events_pipeline(
        settings=settings, sst=sst, cache=cache,
        start=start, end=end, bbox_tuple=bbox_tuple,
        min_category=min_category, raw=raw, include_impact=True,
    )

    filename = f"mheat_events_{start}_{end}.csv"
    return StreamingResponse(
        _csv_rows(geojson),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _events_to_geoparquet_bytes(geojson: dict[str, Any]) -> bytes:
    """Serialise the FeatureCollection as GeoParquet v1.0 bytes."""
    import geopandas as gpd
    from shapely.geometry import shape

    records: list[dict[str, Any]] = []
    geoms: list[Any] = []
    for f in geojson.get("features", []) or []:
        props = dict(f.get("properties") or {})
        props["event_id"] = f.get("id") or props.get("event_id")
        impact = props.pop("impact", None) or {}
        props["n_aquaculture_sites"] = impact.get("n_aquaculture_sites", 0)
        props["mpa_area_km2"] = impact.get("mpa_area_km2", 0.0)
        props["seagrass_area_km2"] = impact.get("seagrass_area_km2", 0.0)
        centroid = props.pop("centroid", None) or [None, None]
        props["centroid_lon"] = centroid[0] if len(centroid) > 0 else None
        props["centroid_lat"] = centroid[1] if len(centroid) > 1 else None
        records.append(props)
        geoms.append(shape(f["geometry"]) if f.get("geometry") else None)

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")
    buf = io.BytesIO()
    gdf.to_parquet(buf, index=False, compression="snappy")
    return buf.getvalue()


@router.get(
    "/events.parquet",
    summary="Detected MHW events as GeoParquet v1.0",
    description="Streams the catalog as GeoParquet v1.0 (EPSG:4326, snappy-compressed).",
    response_description="Parquet file with one row per event",
    responses={200: {"content": {"application/vnd.apache.parquet": {}}}},
)
def list_events_parquet(
    bbox: str | None = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
    start: date | None = Query(None, description="YYYY-MM-DD (required)"),
    end: date | None = Query(None, description="YYYY-MM-DD (required)"),
    min_category: int = Query(
        1, ge=1, le=5, description="Hobday 2018 category filter (I=1…V=5)",
    ),
    raw: bool = Query(False, description="Return raw per-pixel events instead of clusters"),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> Response:
    """GeoParquet flavour of :func:`list_events`.

    Same default-window behaviour as :func:`list_events_csv`.
    """
    bbox_tuple = _parse_bbox(bbox)
    start, end = _resolve_dates(start, end, sst)
    geojson = _events_pipeline(
        settings=settings, sst=sst, cache=cache,
        start=start, end=end, bbox_tuple=bbox_tuple,
        min_category=min_category, raw=raw, include_impact=True,
    )

    blob = _events_to_geoparquet_bytes(geojson)
    filename = f"mheat_events_{start}_{end}.parquet"
    return Response(
        content=blob,
        media_type="application/vnd.apache.parquet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/events/{event_id}",
    summary="Single MHW event lookup as a GeoJSON Feature",
    description="Returns one event by id (per-pixel ``mhw-NNNNNN`` or cluster ``mhw-cluster-NNNN``). ``start``/``end`` are required.",
)
def get_event(
    event_id: str = Path(
        ..., description="Per-pixel id ``mhw-NNNNNN`` or cluster id ``mhw-cluster-NNNN``",
    ),
    start: date | None = Query(None, description="YYYY-MM-DD (required)"),
    end: date | None = Query(None, description="YYYY-MM-DD (required)"),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> dict[str, Any]:
    _require_dates(start, end)
    assert start is not None  # noqa: S101 — validated above
    assert end is not None  # noqa: S101 — validated above
    ev = _find_event_by_id(settings, sst, cache, event_id, start, end)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id!r} not found")
    return ev.to_feature()


@router.get(
    "/events/{event_id}/series",
    summary="SST, climatology & threshold time-series around an event",
    description="Returns SST / climatology / 90th-pct-threshold around a single event at the given location for a Hobday-style chart.",
    response_description="JSON with parallel arrays: time, sst, seas, thresh",
)
def event_series(
    event_id: str = Path(
        ..., description="Per-pixel id ``mhw-NNNNNN`` or cluster id ``mhw-cluster-NNNN``",
    ),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees"),
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees"),
    pad_days: int = Query(30, ge=1, le=120, description="Days to pad around the event"),
    date_start: date | None = Query(
        None, alias="start", description="Window start (YYYY-MM-DD); defaults to event ± ``pad_days``",
    ),
    date_end: date | None = Query(
        None, alias="end", description="Window end (YYYY-MM-DD); defaults to event ± ``pad_days``",
    ),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> dict[str, Any]:
    """Return SST & climatology series centred on the given point/event."""
    import numpy as np
    import pandas as pd

    baseline = _load_baseline_or_503(sst, settings)

    if date_start is None or date_end is None:
        # Without dates we can't subset the cube to find the event — re-run
        # detection over a generous window the cache already covers.
        extent = sst.cube_extent()
        if extent is None:
            raise HTTPException(
                status_code=400,
                detail="start and end query params are required (cache is empty)",
            )
        ev = _find_event_by_id(settings, sst, cache, event_id, extent[0], extent[1])
        if ev is None:
            raise HTTPException(
                status_code=404,
                detail=f"Event {event_id!r} not found in cached cube",
            )
        ev_start = date.fromisoformat(ev.date_start)
        ev_end = date.fromisoformat(ev.date_end)
        date_start = ev_start - timedelta(days=pad_days)
        date_end = ev_end + timedelta(days=pad_days)

    ds = _load_sst_or_503(sst, date_start, date_end)
    var_name = _select_sst_var(ds)
    da = _normalize_latlon(ds[var_name])
    try:
        point = da.sel(latitude=lat, longitude=lon, method="nearest")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Point out of range: {exc}") from exc

    times = point["time"].values
    values = np.asarray(point.values, dtype="float64")
    seas, thresh = baseline.expand_point(times, lat=lat, lon=lon)
    seas = np.asarray(seas, dtype="float64")
    thresh = np.asarray(thresh, dtype="float64")

    def _safe(v: float) -> float | None:
        return None if not np.isfinite(v) else round(float(v), 3)

    return {
        "event_id": event_id,
        "lon": round(float(point["longitude"].values), 4),
        "lat": round(float(point["latitude"].values), 4),
        "times": [str(pd.Timestamp(t).date()) for t in times],
        "sst": [_safe(v) for v in values],
        "seas": [_safe(v) for v in seas],
        "thresh": [_safe(v) for v in thresh],
        "variable": var_name,
    }
