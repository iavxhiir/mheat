"""Marine Heatwave detection using the Hobday et al. (2016) method.

We wrap the ``marineHeatWaves`` Python library (Oliver, 2019) which is the
canonical reference implementation of Hobday 2016. ``detect_series`` operates
on a single 1-D time series; ``detect_cube`` applies it pixel-wise across an
xarray DataArray and aggregates events.

Category rule used by downstream consumers matches Hobday 2018:

    cat_idx = floor((SST_max - clim) / (threshold - clim))

capped at I..V ⇒ Moderate, Strong, Severe, Extreme, Super-Extreme.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from .climatology import Climatology
from .telemetry import span

logger = logging.getLogger(__name__)

CATEGORY_NAMES = ["I Moderate", "II Strong", "III Severe", "IV Extreme", "V Super-Extreme"]


@dataclass
class MhwEvent:
    """One detected marine heatwave event (possibly pixel-aggregated)."""

    event_id: str
    date_start: str
    date_end: str
    date_peak: str
    duration_days: int
    intensity_max: float
    intensity_mean: float
    intensity_cumulative: float
    category: int  # 1..5
    category_name: str
    centroid_lon: float
    centroid_lat: float
    bbox: list[float] = field(default_factory=list)  # [lon_min, lat_min, lon_max, lat_max]
    n_pixels: int = 1
    # Optional precomputed GeoJSON-style geometry. When `cluster_events`
    # unions all member pixel cells via shapely, it stores the resulting
    # (Multi)Polygon mapping here so `to_feature` can render the organic
    # cluster shape instead of the axis-aligned bounding rectangle. Older
    # callers that build a `MhwEvent` directly without `geometry` set
    # continue to get the rectangle fallback (back-compatible).
    geometry: dict[str, Any] | None = None

    def to_feature(self) -> dict[str, Any]:
        """GeoJSON Feature — uses the precomputed cluster shape when present,
        otherwise falls back to the bbox rectangle (per-pixel events)."""
        if self.geometry is not None:
            geom = self.geometry
        else:
            lon_min, lat_min, lon_max, lat_max = self.bbox or [
                self.centroid_lon,
                self.centroid_lat,
                self.centroid_lon,
                self.centroid_lat,
            ]
            coords = [
                [lon_min, lat_min],
                [lon_max, lat_min],
                [lon_max, lat_max],
                [lon_min, lat_max],
                [lon_min, lat_min],
            ]
            geom = {"type": "Polygon", "coordinates": [coords]}
        # Per-event publication timestamp — UTC ISO of detection time.
        # Insurance underwriters (persona 8) need a deterministic
        # "when did MHEAT first observe this event" for parametric
        # trigger contracts. We stamp at to_feature() time which equals
        # the request response time; combined with the cube's daily
        # granularity, an insurer can compute "event_start_t + 24 h
        # publishing SLA" parametric triggers.
        from datetime import datetime, timezone
        published_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return {
            "type": "Feature",
            "id": self.event_id,
            "geometry": geom,
            "properties": {
                "event_id": self.event_id,
                "date_start": self.date_start,
                "date_end": self.date_end,
                "date_peak": self.date_peak,
                "duration_days": self.duration_days,
                "intensity_max": self.intensity_max,
                "intensity_mean": self.intensity_mean,
                "intensity_cumulative": self.intensity_cumulative,
                "category": self.category,
                "category_name": self.category_name,
                "n_pixels": self.n_pixels,
                "centroid": [self.centroid_lon, self.centroid_lat],
                "published_at": published_at,
            },
        }


# ---------------------------------------------------------------------
def _category_index(sst_max: float, clim: float, threshold: float) -> int:
    """Map one event peak to Hobday category 1..5."""
    if threshold <= clim:
        return 1
    raw = (sst_max - clim) / (threshold - clim)
    if raw < 1:
        return 1
    idx = int(np.floor(raw))
    return max(1, min(5, idx))


def _times_to_ordinals(times: Sequence[np.datetime64]) -> np.ndarray:
    """Convert datetime64 array → matplotlib-like ordinal ints (days)."""
    # marineHeatWaves expects ordinal day integers.
    py_dates = pd.to_datetime(times).to_pydatetime()
    return np.array([d.toordinal() for d in py_dates])


def _ordinal_to_iso(ord_day: int) -> str:
    """Ordinal day → YYYY-MM-DD."""
    return datetime.fromordinal(int(ord_day)).date().isoformat()


# ---------------------------------------------------------------------
def detect_series(
    times: Sequence[np.datetime64],
    sst: np.ndarray,
    clim_period: tuple[int, int] = (1991, 2020),
) -> dict[str, Any]:
    """Detect MHWs on a single SST time series.

    Args:
        times: 1-D array of datetime64 values.
        sst: 1-D array of SST values (°C), same length as times.
        clim_period: inclusive (start_year, end_year) for the baseline. If the
            supplied series does not cover 30 years, the library falls back
            gracefully using whatever data is available.

    Returns:
        Dict with keys ``mhws`` (list from marineHeatWaves.detect) and ``clim``.
    """
    from marineHeatWaves import detect as mhw_detect

    t = _times_to_ordinals(times)
    sst_arr = np.asarray(sst, dtype="float64")

    # If the series is too short for the 30-year reference window, shorten it
    # to whatever the series actually spans so the detector still runs on
    # short synthetic inputs (unit tests, demo fixture).
    start_year = pd.Timestamp(times[0]).year
    end_year = pd.Timestamp(times[-1]).year
    cs = max(clim_period[0], start_year)
    ce = min(clim_period[1], end_year)
    if ce < cs:
        cs, ce = start_year, end_year

    mhws, clim = mhw_detect(
        t,
        sst_arr,
        climatologyPeriod=[cs, ce],
        pctile=90,
        windowHalfWidth=5,         # 11-day window
        smoothPercentile=True,
        smoothPercentileWidth=31,
        minDuration=5,
        joinAcrossGaps=True,
        maxGap=2,
        maxPadLength=False,
        coldSpells=False,
    )
    return {"mhws": mhws, "clim": clim}


# ---------------------------------------------------------------------
def detect_series_with_baseline(
    times: Sequence[np.datetime64],
    sst: np.ndarray,
    seas: np.ndarray,
    thresh: np.ndarray,
    min_duration: int = 5,
    join_across_gaps: bool = True,
    max_gap: int = 2,
) -> dict[str, Any]:
    """Detect MHWs on a 1-D SST series using a pre-computed baseline.

    This is the Hobday 2016 event-detection stage only, skipping the
    climatology build. ``seas`` and ``thresh`` must already align 1:1 with
    ``times`` (call :meth:`Climatology.expand_point` to produce them). Output
    shape matches :func:`detect_series` so downstream code can consume either
    transparently.
    """
    from scipy import ndimage

    sst_arr = np.asarray(sst, dtype="float64")
    seas_arr = np.asarray(seas, dtype="float64")
    thresh_arr = np.asarray(thresh, dtype="float64")
    t_ord = _times_to_ordinals(times)

    missing = ~np.isfinite(sst_arr)
    exceed = np.where(missing, False, sst_arr > thresh_arr)

    labels, n_raw = ndimage.label(exceed)
    raw_events: list[tuple[int, int]] = []
    for ev_id in range(1, n_raw + 1):
        idx = np.where(labels == ev_id)[0]
        raw_events.append((int(idx[0]), int(idx[-1])))

    # Drop short events.
    kept = [(s, e) for s, e in raw_events if (e - s + 1) >= min_duration]

    # Join across short gaps.
    if join_across_gaps and len(kept) > 1:
        merged: list[tuple[int, int]] = [kept[0]]
        for s, e in kept[1:]:
            ps, pe = merged[-1]
            gap_days = int(t_ord[s] - t_ord[pe]) - 1
            if 0 <= gap_days <= max_gap:
                merged[-1] = (ps, e)
            else:
                merged.append((s, e))
        kept = merged
        kept = [(s, e) for s, e in kept if (e - s + 1) >= min_duration]

    mhws: dict[str, list[Any]] = {
        "time_start": [],
        "time_end": [],
        "time_peak": [],
        "duration": [],
        "intensity_max": [],
        "intensity_mean": [],
        "intensity_cumulative": [],
        "index_start": [],
        "index_end": [],
        "index_peak": [],
    }

    for s, e in kept:
        window = slice(s, e + 1)
        rel = sst_arr[window] - seas_arr[window]
        # Peak index (max anomaly within event, skipping NaNs).
        finite = np.where(np.isfinite(rel))[0]
        if finite.size == 0:
            continue
        peak_rel = int(finite[np.argmax(rel[finite])])
        peak_idx = s + peak_rel
        mhws["index_start"].append(s)
        mhws["index_end"].append(e)
        mhws["index_peak"].append(peak_idx)
        mhws["time_start"].append(int(t_ord[s]))
        mhws["time_end"].append(int(t_ord[e]))
        mhws["time_peak"].append(int(t_ord[peak_idx]))
        mhws["duration"].append(int(e - s + 1))
        with np.errstate(invalid="ignore", all="ignore"):
            mhws["intensity_max"].append(float(np.nanmax(rel)))
            mhws["intensity_mean"].append(float(np.nanmean(rel)))
            mhws["intensity_cumulative"].append(float(np.nansum(rel)))

    mhws["n_events"] = len(mhws["time_start"])  # type: ignore[assignment]

    clim = {
        "seas": seas_arr,
        "thresh": thresh_arr,
        "missing": missing,
    }
    return {"mhws": mhws, "clim": clim}


# ---------------------------------------------------------------------
def detect_cube(
    sst_da: xr.DataArray,
    clim_period: tuple[int, int] = (1991, 2020),
    max_pixels: int = 600,
    baseline: Climatology | None = None,
) -> list[MhwEvent]:
    """Run Hobday detection pixel-wise on an SST cube and aggregate events.

    For MVP performance, pixels are sub-sampled (coarsened) so a 50 k-pixel
    cube still completes in a few seconds. Each per-pixel event is emitted as
    its own MhwEvent; downstream the UI clusters them visually. Cheap,
    transparent, and good enough for the demo.

    Args:
        sst_da: DataArray with dims (time, latitude, longitude).
        clim_period: reference-period years.
        max_pixels: approximate upper bound on pixels actually evaluated.

    Returns:
        List of :class:`MhwEvent`.
    """
    if sst_da.ndim != 3:
        raise ValueError(f"Expected 3D (time, lat, lon) DataArray, got shape {sst_da.shape}")

    from . import metrics as _metrics

    mode = "baseline" if baseline is not None else "legacy"
    _metrics.inc_baseline_used(mode)

    with span(
        "detect_cube",
        shape=str(sst_da.shape),
        mode=mode,
    ):
        return _detect_cube_impl(sst_da, clim_period, max_pixels, baseline)


def _detect_cube_impl(sst_da, clim_period, max_pixels, baseline: Climatology | None = None):
    # Coarsen if needed
    n_lat = sst_da.sizes["latitude"]
    n_lon = sst_da.sizes["longitude"]
    total = n_lat * n_lon
    step = max(1, int(np.ceil(np.sqrt(total / max_pixels))))
    if step > 1:
        sst_da = sst_da.isel(
            latitude=slice(None, None, step),
            longitude=slice(None, None, step),
        )
        logger.info(
            "Coarsened cube from %d → %d pixels (step=%d)",
            total, sst_da.sizes["latitude"] * sst_da.sizes["longitude"], step,
        )

    times = sst_da["time"].values
    lats = sst_da["latitude"].values
    lons = sst_da["longitude"].values
    values = sst_da.values  # (T, Y, X)

    # Approximate half-pixel for bbox rendering.
    lat_step = float(abs(lats[1] - lats[0])) if lats.size > 1 else 0.25
    lon_step = float(abs(lons[1] - lons[0])) if lons.size > 1 else 0.25

    # Pre-compute per-pixel (seas, thresh) from the baseline artifact once so
    # each pixel call doesn't re-index. Skipped when no baseline is supplied
    # (legacy path, used by demo-mode tests and short synthetic cubes).
    seas_grid: np.ndarray | None = None
    thresh_grid: np.ndarray | None = None
    if baseline is not None:
        try:
            seas_grid, thresh_grid = _align_baseline_to_grid(
                baseline, times, lats, lons
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Baseline align failed (%s); falling back to legacy", e
            )
            baseline = None

    events: list[MhwEvent] = []
    event_counter = 0
    for iy, la in enumerate(lats):
        for ix, lo in enumerate(lons):
            series = values[:, iy, ix]
            if not np.isfinite(series).any():
                continue
            try:
                if baseline is not None:
                    assert seas_grid is not None  # noqa: S101 — set with baseline
                    assert thresh_grid is not None  # noqa: S101 — set with baseline
                    res = detect_series_with_baseline(
                        times,
                        series,
                        seas=seas_grid[:, iy, ix],
                        thresh=thresh_grid[:, iy, ix],
                    )
                else:
                    res = detect_series(times, series, clim_period=clim_period)
            except Exception as e:  # noqa: BLE001
                logger.debug("MHW detection failed at (%s,%s): %s", la, lo, e)
                continue
            mhws = res["mhws"]
            clim = res["clim"]
            n = int(mhws.get("n_events", 0)) if isinstance(mhws, dict) else 0
            if n == 0:
                continue

            for k in range(n):
                ts = mhws["time_start"][k]
                te = mhws["time_end"][k]
                tp = mhws["time_peak"][k]
                dur = int(mhws["duration"][k])
                i_max = float(mhws["intensity_max"][k])
                i_mean = float(mhws["intensity_mean"][k])
                i_cum = float(mhws["intensity_cumulative"][k])

                # Align clim & threshold to the peak day
                peak_idx = int(mhws["index_peak"][k])
                clim_at_peak = float(np.asarray(clim["seas"])[peak_idx])
                thresh_at_peak = float(np.asarray(clim["thresh"])[peak_idx])
                sst_at_peak = clim_at_peak + i_max
                cat = _category_index(sst_at_peak, clim_at_peak, thresh_at_peak)

                event_counter += 1
                events.append(
                    MhwEvent(
                        event_id=f"mhw-{event_counter:06d}",
                        date_start=_ordinal_to_iso(ts),
                        date_end=_ordinal_to_iso(te),
                        date_peak=_ordinal_to_iso(tp),
                        duration_days=dur,
                        intensity_max=round(i_max, 3),
                        intensity_mean=round(i_mean, 3),
                        intensity_cumulative=round(i_cum, 3),
                        category=cat,
                        category_name=CATEGORY_NAMES[cat - 1],
                        centroid_lon=round(float(lo), 4),
                        centroid_lat=round(float(la), 4),
                        bbox=[
                            round(float(lo) - lon_step / 2, 4),
                            round(float(la) - lat_step / 2, 4),
                            round(float(lo) + lon_step / 2, 4),
                            round(float(la) + lat_step / 2, 4),
                        ],
                    )
                )
    logger.info("Detected %d per-pixel MHW events", len(events))
    return events


def _align_baseline_to_grid(
    baseline: Climatology,
    times: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Re-grid a baseline to match an arbitrary (times, lats, lons) cube.

    Uses nearest-neighbor spatially (baseline resolution typically matches
    the live SST grid, so this is effectively a straight lookup) and
    DOY-indexing temporally.
    """
    seas_on_grid = baseline.seas.sel(
        latitude=xr.DataArray(lats, dims=["latitude"]),
        longitude=xr.DataArray(lons, dims=["longitude"]),
        method="nearest",
    )
    thresh_on_grid = baseline.thresh.sel(
        latitude=xr.DataArray(lats, dims=["latitude"]),
        longitude=xr.DataArray(lons, dims=["longitude"]),
        method="nearest",
    )
    from .climatology import _doy_index

    idx = _doy_index(times)
    seas = seas_on_grid.isel(dayofyear=idx).values  # (T, lat, lon)
    thresh = thresh_on_grid.isel(dayofyear=idx).values
    return seas, thresh


# ---------------------------------------------------------------------
def filter_events(
    events: list[MhwEvent],
    start: date | None = None,
    end: date | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[MhwEvent]:
    """Filter events by temporal and spatial extent."""
    out: list[MhwEvent] = []
    for e in events:
        e_start = date.fromisoformat(e.date_start)
        e_end = date.fromisoformat(e.date_end)
        if start and e_end < start:
            continue
        if end and e_start > end:
            continue
        if bbox:
            lon_min, lat_min, lon_max, lat_max = bbox
            if (
                e.centroid_lon < lon_min
                or e.centroid_lon > lon_max
                or e.centroid_lat < lat_min
                or e.centroid_lat > lat_max
            ):
                continue
        out.append(e)
    return out


def events_to_geojson(events: list[MhwEvent]) -> dict[str, Any]:
    """Bundle MhwEvent list as a GeoJSON FeatureCollection."""
    return {
        "type": "FeatureCollection",
        "features": [e.to_feature() for e in events],
    }


# ---------------------------------------------------------------------
def _dates_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """True if two date ranges (YYYY-MM-DD strings) overlap inclusively."""
    return not (a_end < b_start or b_end < a_start)


def _union_pixel_geometry(members: list[MhwEvent]) -> dict[str, Any] | None:
    """Union all member pixel cells into a (Multi)Polygon GeoJSON, OR
    return a Point when the cluster is a single pixel (no real "shape").

    Each per-pixel ``MhwEvent`` carries a tiny ``bbox`` that is the grid
    cell of one Copernicus Marine SST pixel (~0.0625° × 0.0625°). The
    union of these cells is the actual spatial footprint of the cluster.

    For ``len(members) == 1`` the cluster has no spatial extent worth
    drawing as a polygon — emitting a Point at the pixel centroid lets
    the frontend render it as an intensity-scaled circle marker, which
    is visually distinct from large clustered events (it's a "spot" not
    a "blob") and avoids the misleading "this MHW occupies a 7×7 km
    square" reading that polygon rendering implies.

    Returns ``None`` if shapely is unavailable, no member has a usable
    bbox, or the union fails for any reason; callers fall back to the
    bbox-rectangle in :meth:`MhwEvent.to_feature`.
    """
    if not members:
        return None
    # Single-pixel cluster — emit Point at the pixel centroid. Bypasses
    # shapely entirely so it works even without the optional dep.
    if len(members) == 1 and members[0].bbox and len(members[0].bbox) == 4:
        m = members[0]
        lon_min, lat_min, lon_max, lat_max = m.bbox
        return {
            "type": "Point",
            "coordinates": [
                round((lon_min + lon_max) / 2, 4),
                round((lat_min + lat_max) / 2, 4),
            ],
        }
    try:
        from shapely.geometry import box, mapping
        from shapely.ops import unary_union
    except ImportError:
        return None
    boxes = [box(*e.bbox) for e in members if e.bbox and len(e.bbox) == 4]
    if not boxes:
        return None
    try:
        merged = unary_union(boxes)
    except Exception as exc:  # noqa: BLE001
        logger.info("union_pixel_geometry failed (%s); falling back to bbox", exc)
        return None
    geom = mapping(merged)
    # Round coords to 4 decimals (~11 m) to keep payloads small and
    # make the response byte-stable for the reproducibility manifest.
    def _round(c: Any) -> Any:
        if isinstance(c, (int, float)):
            return round(float(c), 4)
        if isinstance(c, (list, tuple)):
            return [_round(x) for x in c]
        return c
    geom["coordinates"] = _round(geom["coordinates"])
    return geom


def cluster_events(
    events: list[MhwEvent],
    max_centroid_distance_deg: float = 1.5,
) -> list[MhwEvent]:
    """Group per-pixel events into contiguous space-time clusters.

    Two events belong to the same cluster when:
    * their date ranges overlap (inclusive), AND
    * their centroid great-circle (approx. Euclidean on lon/lat) distance
      is < ``max_centroid_distance_deg`` degrees.

    Each resulting cluster is a single :class:`MhwEvent` whose bbox spans
    every member, ``n_pixels`` is the member count, peak/intensity values
    are the MAX over members, and duration is the longest member duration.

    Implemented with a Union-Find over the input list to guarantee transitive
    closure — two events connected through a chain of neighbours end up in
    the same cluster even if they are individually far apart.
    """
    with span("cluster_events", n=len(events)):
        return _cluster_events_impl(events, max_centroid_distance_deg)


def _cluster_events_impl(events, max_centroid_distance_deg):
    n = len(events)
    if n == 0:
        return []

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        ei = events[i]
        for j in range(i + 1, n):
            ej = events[j]
            # Cheap rejection by centroid distance first.
            dlon = ei.centroid_lon - ej.centroid_lon
            dlat = ei.centroid_lat - ej.centroid_lat
            if (dlon * dlon + dlat * dlat) ** 0.5 >= max_centroid_distance_deg:
                continue
            if not _dates_overlap(ei.date_start, ei.date_end, ej.date_start, ej.date_end):
                continue
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clustered: list[MhwEvent] = []
    for cluster_idx, (_, members) in enumerate(sorted(groups.items()), start=1):
        mem = [events[i] for i in members]
        # Union bbox
        lon_mins = [e.bbox[0] for e in mem if e.bbox]
        lat_mins = [e.bbox[1] for e in mem if e.bbox]
        lon_maxs = [e.bbox[2] for e in mem if e.bbox]
        lat_maxs = [e.bbox[3] for e in mem if e.bbox]
        bbox = [min(lon_mins), min(lat_mins), max(lon_maxs), max(lat_maxs)] if lon_mins else []

        # Pick the member with the highest intensity as the "anchor" for peak date.
        anchor = max(mem, key=lambda e: e.intensity_max)
        # Span dates
        d_start = min(e.date_start for e in mem)
        d_end = max(e.date_end for e in mem)
        dur = max(e.duration_days for e in mem)
        i_max = max(e.intensity_max for e in mem)
        i_mean = max(e.intensity_mean for e in mem)
        i_cum = max(e.intensity_cumulative for e in mem)
        cat = max(e.category for e in mem)
        # Centroid of the bbox.
        if bbox:
            c_lon = (bbox[0] + bbox[2]) / 2
            c_lat = (bbox[1] + bbox[3]) / 2
        else:
            c_lon = anchor.centroid_lon
            c_lat = anchor.centroid_lat

        # Union the member pixel cells into the actual cluster footprint.
        # Falls back to the bbox rectangle if shapely is unavailable or the
        # union fails (defensive — a render failure must not break /api/events).
        cluster_geom = _union_pixel_geometry(mem)

        clustered.append(
            MhwEvent(
                event_id=f"mhw-cluster-{cluster_idx:04d}",
                date_start=d_start,
                date_end=d_end,
                date_peak=anchor.date_peak,
                duration_days=dur,
                intensity_max=round(i_max, 3),
                intensity_mean=round(i_mean, 3),
                intensity_cumulative=round(i_cum, 3),
                category=cat,
                category_name=CATEGORY_NAMES[cat - 1],
                centroid_lon=round(c_lon, 4),
                centroid_lat=round(c_lat, 4),
                bbox=[round(v, 4) for v in bbox] if bbox else [],
                n_pixels=len(mem),
                geometry=cluster_geom,
            )
        )
    # Sort clusters by severity then start date so the UI list is stable.
    clustered.sort(key=lambda e: (-e.category, -e.n_pixels, e.date_start))
    return clustered
