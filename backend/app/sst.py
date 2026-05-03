"""Sea Surface Temperature data access.

Cache-first design: the service reads SST cubes from a local Zarr store
(``settings.zarr_store``) populated by the prefetch hook on startup and the
lazy on-miss filler in :meth:`SSTProvider.load_range`. Only the gap between
what the cache holds and what a request asks for ever hits Copernicus Marine.

* :meth:`SSTProvider.load_range` — slice ``[start, end]`` from the cube,
  fetching and merging any missing window from CMS first.
* :meth:`SSTProvider.load_climatology` — open the Hobday climatology Zarr
  produced by ``scripts/bootstrap_climatology.py`` (memoized per-process).
* :meth:`SSTProvider.prefetch_warm_window` — startup hook that ensures the
  cube contains at least the last ``WARM_WINDOW_DAYS`` of data so the first
  /api/* request after boot is served from disk.

The Copernicus SDK is imported lazily so processes that only read the cache
(workers, UIs hitting cached endpoints) don't pay the SDK import cost.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .cache import CacheStore
from .climatology import Climatology
from .config import Settings

logger = logging.getLogger(__name__)

# Surface depth for thetao products (Copernicus 4.2 km Med physics: ~1.0182 m).
_SURFACE_DEPTH_M = 1.02
_DEPTH_DIM_CANDIDATES = ("depth", "deptht", "nav_dep", "lev")

# Copernicus NRT L4 SST has a rolling window (~1 year, currently 2008→present).
# Anything older than this falls back to the multi-year reanalysis (1987→2026-03)
# so the service can serve 1987-present without gaps. Forecast covers
# present+10 days. Verified against the CMS catalogue 2026-04.
NRT_LOOKBACK_DAYS = 365

# Default warm-window prefetched on first boot (last 90 days of NRT).
# Small enough that the first-boot CMS pull completes in under a minute on
# a typical line, large enough that the UI's default "last 90 days" view
# renders entirely from disk.
WARM_WINDOW_DAYS = 90


class CMSCredentialsMissingError(RuntimeError):
    """Raised when a CMS fetch is required but credentials are absent."""


class SSTCacheMissingError(RuntimeError):
    """Raised when the cache is empty and CMS is unreachable / unconfigured."""


# Module-level state for /api/freshness — tracks the most recent live CMS pull
# so the dashboard can show "🛰️ Updated 3 minutes ago" instead of a generic
# spinner. Mutated by `_fetch_and_merge` start/finish; read by the freshness
# router. Per-process; multi-replica deployments should rely on the cube
# extent field instead, which is canonical.
_LIVE_PULL_STATE: dict[str, str | bool | None] = {
    "in_progress": False,
    "start_date": None,
    "end_date": None,
    "started_at": None,
    "last_success_at": None,
    "last_error_at": None,
    "last_error": None,
}


def get_live_pull_state() -> dict[str, str | bool | None]:
    """Return a snapshot of the current live-pull state (copy, not a ref)."""
    return dict(_LIVE_PULL_STATE)


@dataclass
class SSTProvider:
    """Cache-backed SST provider with lazy fill from Copernicus Marine."""

    settings: Settings
    cache: CacheStore

    # ------------------------------------------------------------------ cube
    @property
    def zarr_path(self) -> Path:
        return Path(self.settings.zarr_store)

    def cube(self) -> xr.Dataset | None:
        """Open the on-disk SST cube, or ``None`` if it isn't materialised yet."""
        if not self.cache.zarr_exists():
            return None
        try:
            return xr.open_zarr(str(self.zarr_path), consolidated=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to open SST cube at %s: %s", self.zarr_path, exc)
            return None

    def cube_extent(self) -> tuple[date, date] | None:
        """Return ``(first_day, last_day)`` of the cached cube, or ``None``."""
        ds = self.cube()
        if ds is None or "time" not in ds.dims or ds.sizes["time"] == 0:
            return None
        times = ds["time"].values
        return (
            pd.Timestamp(times[0]).date(),
            pd.Timestamp(times[-1]).date(),
        )

    # ------------------------------------------------------------------ load
    def load(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> xr.Dataset:
        """Return the cached cube, optionally sliced to ``[start, end]``.

        When ``start``/``end`` are omitted the whole cached cube is returned —
        used by anomaly extent reporting and the UI's "what do we have?" probe.
        """
        if start is not None and end is not None:
            return self.load_range(start, end)
        ds = self.cube()
        if ds is None:
            raise SSTCacheMissingError(
                f"SST cache is empty at {self.zarr_path}. "
                "Run scripts/bootstrap_climatology.py (which also seeds the "
                "cube) or wait for the startup prefetch to populate it."
            )
        return ds

    def load_range(self, start: date, end: date) -> xr.Dataset:
        """Slice ``[start, end]`` from the cube, lazy-filling missing gaps."""
        ds = self.cube()
        if ds is None:
            self._fetch_and_merge(start, end)
            ds = self.cube()
            if ds is None:
                raise SSTCacheMissingError(
                    f"Failed to populate SST cache at {self.zarr_path} for "
                    f"{start}..{end}; check Copernicus credentials and connectivity."
                )
            return _slice_inclusive(ds, start, end)

        gaps = _missing_ranges(self.cube_extent(), start, end)
        for gap_start, gap_end in gaps:
            logger.info(
                "Cache miss for %s..%s; fetching from CMS", gap_start, gap_end,
            )
            self._fetch_and_merge(gap_start, gap_end)

        ds = self.cube()
        assert ds is not None  # noqa: S101 — re-opened after fetch
        return _slice_inclusive(ds, start, end)

    # Backwards-compat alias used by callers that explicitly want a CMS pull.
    # In the cache-first world this is just ``load_range``: the merge ensures
    # the slab ends up on disk regardless.
    def load_live(
        self,
        start: date,
        end: date,
        bbox: tuple[float, float, float, float] | None = None,  # noqa: ARG002
        _today: date | None = None,  # noqa: ARG002
    ) -> xr.Dataset:
        return self.load_range(start, end)

    # --------------------------------------------------------- climatology
    def load_climatology(self) -> Climatology | None:
        """Open the Hobday climatology Zarr, or ``None`` if absent."""
        from . import metrics as _metrics

        path = Path(self.settings.climatology_store)
        before = _open_climatology_cached.cache_info()
        try:
            clim = _open_climatology_cached(str(path))
        except FileNotFoundError:
            logger.info("Climatology artifact not found at %s", path)
            return None
        after = _open_climatology_cached.cache_info()
        if after.hits > before.hits:
            _metrics.inc_climatology_cache_hit()
        else:
            _metrics.inc_climatology_cache_miss()
        return clim

    # ------------------------------------------------------------ prefetch
    def prefetch_warm_window(self, today: date | None = None) -> bool:
        """Ensure the cube contains the last ``WARM_WINDOW_DAYS`` of NRT data.

        Returns ``True`` if a fetch was performed (or the cache already covered
        the window), ``False`` if credentials are missing or the fetch failed.
        Boot continues either way — the request path falls back to clear 503s.
        """
        if not self.settings.credentials_present():
            logger.warning(
                "Skipping startup prefetch: Copernicus credentials are not set; "
                "set COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD."
            )
            return False
        today = today or datetime.now(UTC).date()
        start = today - timedelta(days=WARM_WINDOW_DAYS)
        extent = self.cube_extent()
        if extent is not None and extent[0] <= start and extent[1] >= today - timedelta(days=2):
            logger.info(
                "Cache already covers warm window (%s..%s ⊇ %s..%s); skipping prefetch",
                extent[0], extent[1], start, today,
            )
            return True
        try:
            self.load_range(start, today)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Startup prefetch failed: %s", exc)
            return False

    # -------------------------------------------------------------- internal
    def _select_dataset(
        self, start: date, end: date, today: date,
    ) -> tuple[str, str, bool]:
        """Pick the right Copernicus product for the request window.

        Three-way routing implements the proposal's multi-source fusion claim:

        * ``start > today`` → **forecast** (analysis-and-forecast, ``thetao``, 3-D).
        * Window entirely before the NRT rolling window
          (``end < today - NRT_LOOKBACK_DAYS``) → **reanalysis** (multi-year
          ``thetao``, 3-D) — unlocks 1987-present coverage (Med reanalysis floor).
        * Otherwise → **NRT** (``analysed_sst``, 2-D, default for the recent year).
        """
        if start > today:
            return self.settings.cms_forecast_product, "thetao", True
        nrt_cutoff = today - timedelta(days=NRT_LOOKBACK_DAYS)
        if end < nrt_cutoff:
            return self.settings.cms_reanalysis_product, "thetao", True
        return self.settings.cms_nrt_product, "analysed_sst", False

    def _fetch_cms(self, start: date, end: date) -> xr.Dataset:
        """Pull a CMS subset for ``[start, end]`` and harmonise it to ``analysed_sst``."""
        if not self.settings.credentials_present():
            raise CMSCredentialsMissingError(
                "Copernicus Marine credentials are not set. "
                "Provide COPERNICUSMARINE_SERVICE_USERNAME and "
                "COPERNICUSMARINE_SERVICE_PASSWORD in .env."
            )
        # Lazy import: keeps cache-only workers from paying the SDK import cost.
        import copernicusmarine

        today = datetime.now(UTC).date()
        dataset_id, variable, needs_surface = self._select_dataset(start, end, today)
        bbox = self.settings.bbox_tuple
        lon_min, lat_min, lon_max, lat_max = bbox
        logger.info(
            "Fetching CMS %s var=%s %s→%s bbox=%s", dataset_id, variable, start, end, bbox,
        )

        out_dir = self.cache.cache_dir / "cms"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{dataset_id}_{variable}_{start:%Y%m%d}_{end:%Y%m%d}.nc"

        kwargs: dict[str, object] = {
            "dataset_id": dataset_id,
            "minimum_longitude": lon_min, "maximum_longitude": lon_max,
            "minimum_latitude": lat_min, "maximum_latitude": lat_max,
            "start_datetime": f"{start.isoformat()}T00:00:00",
            "end_datetime": f"{end.isoformat()}T23:59:59",
            "variables": [variable],
            "output_directory": str(out_dir), "output_filename": out_name,
            "username": self.settings.cms_username,
            "password": self.settings.cms_password,
            "overwrite": True,
        }
        if needs_surface:
            kwargs["minimum_depth"], kwargs["maximum_depth"] = 1.0, 1.1
        copernicusmarine.subset(**kwargs)

        ds = xr.open_dataset(out_dir / out_name)
        if needs_surface:
            ds = _to_surface_sst(ds)
        ds = _normalize_coords(ds)
        return _ensure_celsius(ds)

    def _fetch_and_merge(self, start: date, end: date) -> None:
        """Fetch ``[start, end]`` from CMS and merge it into the on-disk Zarr."""
        # Record the pull start so /api/freshness can advertise progress.
        from datetime import datetime as _dt
        from . import sst as _sst_mod  # late import to avoid module-self-cycle issues
        _sst_mod._LIVE_PULL_STATE.update({
            "in_progress": True,
            "start_date": str(start),
            "end_date": str(end),
            "started_at": _dt.utcnow().isoformat() + "Z",
        })
        try:
            self._fetch_and_merge_impl(start, end)
            _sst_mod._LIVE_PULL_STATE.update({
                "in_progress": False,
                "last_success_at": _dt.utcnow().isoformat() + "Z",
                "last_error": None,
            })
        except Exception as exc:
            _sst_mod._LIVE_PULL_STATE.update({
                "in_progress": False,
                "last_error_at": _dt.utcnow().isoformat() + "Z",
                "last_error": str(exc)[:200],
            })
            raise

    def _fetch_and_merge_impl(self, start: date, end: date) -> None:
        new_ds = self._fetch_cms(start, end)
        new_ds = new_ds[["analysed_sst"]] if "analysed_sst" in new_ds.data_vars else new_ds

        existing = self.cube()
        if existing is None:
            merged = new_ds
        else:
            try:
                # Different CMS products live on different grids (NRT L4 obs at
                # 1/16°, reanalysis at 1/24°, forecast at 1/24°). Without
                # alignment, xr.concat outer-joins the lat/lon and the cube
                # ends up with a hybrid grid that matches *neither* product —
                # which then mismatches the climatology and breaks /api/anomaly.
                # Snap the new slab to the existing cube grid before concat.
                aligned = new_ds[["analysed_sst"]].interp(
                    latitude=existing["latitude"],
                    longitude=existing["longitude"],
                    method="linear",
                    kwargs={"fill_value": np.nan},
                )
                merged = xr.concat(
                    [existing[["analysed_sst"]], aligned],
                    dim="time",
                    coords="minimal",
                    compat="override",
                )
                # Drop duplicates (overlap with existing window) and keep sorted.
                _, unique_idx = np.unique(merged["time"].values, return_index=True)
                merged = merged.isel(time=np.sort(unique_idx)).sortby("time")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Cube concat failed (%s); rebuilding from new fetch only", exc,
                )
                merged = new_ds

        # Drop any inherited encoding (chunk shapes from the previous Zarr
        # write, encoding from the upstream NetCDF) before re-writing — when
        # the cube grows the inherited chunk shape no longer aligns with the
        # new dask layout and xarray refuses with "encoding['chunks']=...
        # would overlap multiple dask chunks". Letting Zarr pick chunks
        # afresh keeps the merge robust at the cost of one extra rechunk.
        for var in merged.data_vars:
            merged[var].encoding = {}
        for coord in merged.coords:
            merged[coord].encoding = {}

        # Re-chunk so to_zarr doesn't trip on "Zarr requires uniform chunk
        # sizes" or "Final chunk must be the same size or smaller than the
        # first" — those errors fire when concat or the upstream CMS
        # download leaves uneven chunks (e.g. existing 30 d + new 91 d →
        # time=(30, 91), or CMS reanalysis → lat=(126,126,115,11,129)).
        #
        # Use a moderate time chunk (≈1 year) instead of "all in one slab"
        # so multi-year cubes don't bust the Blosc 2 GB per-chunk buffer
        # ceiling. lat/lon stay full-slab — they're small enough.
        time_chunk = max(1, min(int(merged.sizes.get("time", 1)), 365))
        merged = merged.chunk({
            "time": time_chunk,
            **{d: -1 for d in merged.dims if d != "time"},
        })

        # Atomic-ish write: write to a sibling, swap in.
        tmp_path = self.zarr_path.parent / (self.zarr_path.name + ".tmp")
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_zarr(str(tmp_path), mode="w", consolidated=True)
        if self.zarr_path.exists():
            shutil.rmtree(self.zarr_path, ignore_errors=True)
        tmp_path.rename(self.zarr_path)
        logger.info(
            "Cache updated: %s now spans %s..%s (%d days)",
            self.zarr_path,
            pd.Timestamp(merged["time"].values[0]).date(),
            pd.Timestamp(merged["time"].values[-1]).date(),
            int(merged.sizes["time"]),
        )


@lru_cache(maxsize=4)
def _open_climatology_cached(path_str: str) -> Climatology:
    """Memoize ``Climatology.open`` so request paths don't reopen the zarr."""
    clim = Climatology.open(Path(path_str))
    bbox = clim.attrs.get("bbox")
    years = (clim.attrs.get("clim_start"), clim.attrs.get("clim_end"))
    logger.info("Loaded climatology from %s, bbox=%s, years=%s", path_str, bbox, years)
    return clim


def _slice_inclusive(ds: xr.Dataset, start: date, end: date) -> xr.Dataset:
    """Return the time-inclusive slab ``[start, end]``."""
    return ds.sel(time=slice(start.isoformat(), end.isoformat()))


def _missing_ranges(
    extent: tuple[date, date] | None, start: date, end: date,
) -> list[tuple[date, date]]:
    """Return the date sub-ranges of ``[start, end]`` not covered by ``extent``."""
    if extent is None:
        return [(start, end)]
    cube_start, cube_end = extent
    gaps: list[tuple[date, date]] = []
    if start < cube_start:
        gaps.append((start, min(end, cube_start - timedelta(days=1))))
    if end > cube_end:
        gaps.append((max(start, cube_end + timedelta(days=1)), end))
    return [g for g in gaps if g[0] <= g[1]]


def _to_surface_sst(ds: xr.Dataset) -> xr.Dataset:
    """Reduce a 3-D ``thetao`` cube to 2-D ``analysed_sst``.

    Defensive against alternate depth-dim spellings (``depth``, ``deptht``,
    ``nav_dep``, ``lev``).
    """
    depth_dim = next((d for d in _DEPTH_DIM_CANDIDATES if d in ds.dims), None)
    if depth_dim is not None:
        ds = ds.sel({depth_dim: _SURFACE_DEPTH_M}, method="nearest")
        if depth_dim in ds.dims:
            ds = ds.squeeze(depth_dim, drop=True)
        if depth_dim in ds.coords:
            ds = ds.drop_vars(depth_dim)
    if "thetao" in ds.data_vars:
        ds = ds.rename({"thetao": "analysed_sst"})
        ds["analysed_sst"].attrs.setdefault("long_name", "Sea surface temperature (from thetao)")
    return ds


def _normalize_coords(ds: xr.Dataset) -> xr.Dataset:
    """Rename ``lat``/``lon`` (and CMS aliases) to ``latitude``/``longitude``."""
    rename = {}
    for src, dst in (("lat", "latitude"), ("lon", "longitude"),
                     ("nav_lat", "latitude"), ("nav_lon", "longitude")):
        if src in ds.coords or src in ds.dims:
            rename[src] = dst
    return ds.rename(rename) if rename else ds


def _ensure_celsius(ds: xr.Dataset, var: str = "analysed_sst") -> xr.Dataset:
    """Return ``ds`` with ``var`` in °C (NRT L4 ships kelvin)."""
    if var not in ds.data_vars:
        return ds
    units = str(ds[var].attrs.get("units", "")).strip().lower()
    if units in {"kelvin", "k", "degrees_kelvin", "degree_kelvin"}:
        ds = ds.assign({var: ds[var] - 273.15})
        ds[var].attrs["units"] = "degC"
        ds[var].attrs["long_name"] = ds[var].attrs.get("long_name", "Analysed SST")
    return ds


# ---------------------------------------------------------------------------
# Helpers retained for callers (events router, tests) that imported them
# from the old module surface. They now operate on the cached cube only.
# ---------------------------------------------------------------------------
def iter_cube_years(ds: xr.Dataset) -> Iterable[int]:
    """Yield the unique calendar years present in a cube's time axis."""
    if "time" not in ds.dims:
        return iter(())
    return iter(sorted({pd.Timestamp(t).year for t in ds["time"].values}))
