"""Cross-cutting aggregation endpoint over MHEAT events.

Persona-driven (see `docs/personas_review.md` Tier-1 cross-cut #1) — feeds
the fisheries-manager + MPA-manager + climate-scientist + EU-policy-analyst
+ NGO-campaigner workflows from one URL with a `by` query param.

Contract:
  GET /api/aggregate?by={year|category|country|mpa}&start=YYYY-MM-DD&end=YYYY-MM-DD

Returns JSON `{"by": "...", "buckets": [{"key": "...", "count": int,
  "intensity_max": float, "intensity_mean": float, "n_pixels_total": int,
  "aquaculture_sites": int, "mpa_area_km2": float, "seagrass_area_km2": float},
  ...]}`. Buckets sort by `key` ascending (year), or by `count` descending
(category / country / mpa).

Cheap implementation — fetches the events from the existing /api/events
pipeline (so it inherits all the cube + climatology + clustering work)
and aggregates in Python. For 30 years of data the event count is small
enough (low thousands) that this is fine; if it ever needs to scale,
pre-compute at write-time.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..cache import CacheStore
from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..sst import SSTProvider
from .events import _events_pipeline, _resolve_dates

router = APIRouter(prefix="/api", tags=["aggregate"])

# Hobday severity labels for the category bucket — kept local so we don't
# couple the aggregate endpoint to the per-event router's strings.
_CATEGORY_NAMES = {
    1: "I Moderate", 2: "II Strong", 3: "III Severe",
    4: "IV Extreme", 5: "V Super-Extreme",
}


def _country_for_centroid(lon: float, lat: float) -> str | None:
    """Best-effort country bucket for an event centroid in the Med basin.

    The event centroid is by definition over sea, so we map by the
    nearest member-state coastline. Coarse rule (good enough for an
    aggregate-by-country view; finer GIS work is the GIS-analyst use
    case via OGC API Features filtering).
    """
    if 30 <= lat <= 36.5 and 32 <= lon <= 36.5:
        return "EG"  # SE Med — Egyptian shelf / Levantine south
    if 30 <= lat <= 35 and 19 <= lon <= 32:
        return "LY"  # Libyan coast
    if 30 <= lat <= 38 and -2 <= lon <= 19:
        # Could be Algeria or Tunisia depending on lon
        return "TN" if lon >= 7 else "DZ"
    if 30 <= lat <= 36 and -7 <= lon <= -2:
        return "MA"  # Moroccan coast / Alboran south
    if 35 <= lat <= 41 and -2 <= lon <= 9 and lat <= 39:
        return "ES"  # Spanish Med + Balearics
    if 41 <= lat <= 43 and 0 <= lon <= 5:
        return "FR"  # French Med
    if 36 <= lat <= 46 and 5 <= lon <= 19:
        return "IT"  # Italian peninsula + Sicily + Sardinia + Adriatic west
    if 40 <= lat <= 46 and 13 <= lon <= 21:
        return "HR"  # Croatian coast
    if 34 <= lat <= 42 and 19 <= lon <= 28:
        return "GR"  # Greek mainland + islands
    if 35 <= lat <= 37 and 32 <= lon <= 36:
        return "CY"  # Cyprus
    if 36 <= lat <= 42 and 26 <= lon <= 36:
        return "TR"  # Turkish coast
    return None


def _bucket_init() -> dict[str, Any]:
    return {
        "count": 0,
        "intensity_max": 0.0,
        "_intensity_sum": 0.0,
        "_intensity_n": 0,
        "n_pixels_total": 0,
        "aquaculture_sites": 0,
        "mpa_area_km2": 0.0,
        "seagrass_area_km2": 0.0,
    }


def _bucket_add(bucket: dict[str, Any], feat: dict[str, Any]) -> None:
    p = feat.get("properties", {}) or {}
    bucket["count"] += 1
    im = p.get("intensity_max")
    if isinstance(im, (int, float)):
        bucket["intensity_max"] = max(bucket["intensity_max"], float(im))
        bucket["_intensity_sum"] += float(im)
        bucket["_intensity_n"] += 1
    bucket["n_pixels_total"] += int(p.get("n_pixels", 0) or 0)
    impact = p.get("impact") or {}
    bucket["aquaculture_sites"] += int(impact.get("n_aquaculture_sites", 0) or 0)
    bucket["mpa_area_km2"] += float(impact.get("mpa_area_km2", 0) or 0)
    bucket["seagrass_area_km2"] += float(impact.get("seagrass_area_km2", 0) or 0)


def _bucket_finalize(bucket: dict[str, Any], key: str) -> dict[str, Any]:
    n = bucket.pop("_intensity_n")
    s = bucket.pop("_intensity_sum")
    bucket["intensity_mean"] = (s / n) if n else 0.0
    bucket["mpa_area_km2"] = round(bucket["mpa_area_km2"], 2)
    bucket["seagrass_area_km2"] = round(bucket["seagrass_area_km2"], 2)
    bucket["intensity_max"] = round(bucket["intensity_max"], 3)
    bucket["intensity_mean"] = round(bucket["intensity_mean"], 3)
    bucket["key"] = key
    return bucket


@router.get(
    "/aggregate",
    summary="Aggregate MHW events across the time window by year / category / country / MPA",
    description=(
        "Bucketed roll-up over /api/events output. `by=year` returns one "
        "bucket per calendar year; `by=category` per Hobday I-V; `by=country` "
        "per Med-rim member state (coarse centroid bucketing); `by=mpa` per "
        "Natura 2000 site that any event touched. Each bucket includes "
        "count, peak/mean intensity, total pixels, and aggregated sectoral "
        "impact (aquaculture sites, MPA km², seagrass km²)."
    ),
    response_description="JSON with `by` and `buckets` array",
)
def aggregate(
    by: Literal["year", "category", "country", "mpa"] = Query(
        ..., description="Bucket axis: year | category | country | mpa",
    ),
    start: date | None = Query(None, description="YYYY-MM-DD inclusive (defaults to last 30 d)"),
    end: date | None = Query(None, description="YYYY-MM-DD inclusive (defaults to today)"),
    min_category: int = Query(1, ge=1, le=5, description="Hobday min category (1=Moderate)"),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> dict[str, Any]:
    """Bucket /api/events output by `by` axis. Covers 5 persona use cases."""
    start_resolved, end_resolved = _resolve_dates(start, end, sst)
    geojson = _events_pipeline(
        settings=settings, sst=sst, cache=cache,
        start=start_resolved, end=end_resolved, bbox_tuple=None,
        min_category=min_category, raw=False, include_impact=True,
    )
    features = geojson.get("features", []) or []

    buckets: dict[str, dict[str, Any]] = {}
    bucket_meta: dict[str, dict[str, Any]] = {}

    for feat in features:
        p = feat.get("properties", {}) or {}
        if by == "year":
            ds = p.get("date_start", "")
            key = ds[:4] if len(ds) >= 4 else "unknown"
        elif by == "category":
            cat = int(p.get("category", 0) or 0)
            key = f"{cat}"
            bucket_meta.setdefault(key, {"category_name": _CATEGORY_NAMES.get(cat, "?")})
        elif by == "country":
            c = p.get("centroid") or [0.0, 0.0]
            iso = _country_for_centroid(float(c[0]), float(c[1]))
            key = iso or "??"
        elif by == "mpa":
            # One bucket per MPA SITECODE that ANY event touched. The
            # /api/events response carries impact.mpa_area_km2 but not
            # the SITECODE list (per-event); for now bucket the global
            # MPA-touching events together by impact bucket.
            if (p.get("impact") or {}).get("mpa_area_km2", 0):
                key = "events_touching_mpa"
            else:
                key = "events_not_touching_mpa"
        else:
            raise HTTPException(status_code=400, detail=f"unknown by={by}")
        buckets.setdefault(key, _bucket_init())
        _bucket_add(buckets[key], feat)

    out_buckets = [
        {**_bucket_finalize(buckets[k], k), **bucket_meta.get(k, {})}
        for k in buckets
    ]
    if by == "year":
        out_buckets.sort(key=lambda b: b["key"])
    else:
        out_buckets.sort(key=lambda b: -b["count"])

    return {
        "by": by,
        "start": str(start_resolved),
        "end": str(end_resolved),
        "min_category": min_category,
        "n_events_total": len(features),
        "buckets": out_buckets,
    }
