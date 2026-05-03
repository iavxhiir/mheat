"""Pre-computed Hobday MHW climatology artifact.

A small xarray-backed container that holds the per-DOY seasonal mean SST and
90th-percentile threshold, persisted to a zarr store. Eliminates the 30-year
recomputation that makes live-mode endpoints otherwise unreviewable.

Artifact schema (``schema_version=1``):

    dims:       dayofyear=366, latitude=N, longitude=M
    variables:
      seas   (float32, degC)  — 31-day smoothed seasonal mean
      thresh (float32, degC)  — 31-day smoothed 90th-percentile threshold

Mirrors the internal climatology construction of ``marineHeatWaves.detect()``
(Oliver 2019, lines ~254-306) but vectorized over the spatial grid so we
only pay the 30-year reduction once, at bootstrap time.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)

CLIMATOLOGY_SCHEMA_VERSION = "1"

# Leap-year baseline: Feb 29 is DOY 60. Matches upstream library convention.
DOY_LEN = 366


@dataclass(frozen=True)
class Climatology:
    """Pre-computed per-DOY Hobday baseline on a regular lat/lon grid."""

    seas: xr.DataArray
    thresh: xr.DataArray
    attrs: dict[str, Any]

    @classmethod
    def open(cls, path: Path | str) -> Climatology:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Climatology zarr not found at {path}")
        ds = xr.open_zarr(str(path), consolidated=True)
        return cls(seas=ds["seas"], thresh=ds["thresh"], attrs=dict(ds.attrs))

    @classmethod
    def from_arrays(
        cls,
        seas: np.ndarray,
        thresh: np.ndarray,
        latitudes: np.ndarray,
        longitudes: np.ndarray,
        attrs: dict[str, Any] | None = None,
    ) -> Climatology:
        """Build from (366, lat, lon) arrays in °C."""
        expected = (DOY_LEN, latitudes.size, longitudes.size)
        if seas.shape != expected:
            raise ValueError(f"seas shape {seas.shape} != expected {expected}")
        if thresh.shape != expected:
            raise ValueError(f"thresh shape {thresh.shape} != expected {expected}")

        coords = {
            "dayofyear": np.arange(1, DOY_LEN + 1, dtype="int16"),
            "latitude": latitudes.astype("float32"),
            "longitude": longitudes.astype("float32"),
        }
        seas_da = xr.DataArray(
            seas.astype("float32"),
            coords=coords,
            dims=["dayofyear", "latitude", "longitude"],
            name="seas",
            attrs={
                "units": "degC",
                "long_name": "Hobday seasonal climatology (31-day smooth)",
            },
        )
        thresh_da = xr.DataArray(
            thresh.astype("float32"),
            coords=coords,
            dims=["dayofyear", "latitude", "longitude"],
            name="thresh",
            attrs={
                "units": "degC",
                "long_name": "Hobday 90th-percentile threshold (31-day smooth)",
            },
        )
        return cls(seas=seas_da, thresh=thresh_da, attrs=attrs or {})

    def save(self, path: Path | str) -> None:
        """Persist to zarr with Blosc LZ4 and consolidated metadata."""
        from numcodecs import Blosc

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ds = xr.Dataset({"seas": self.seas, "thresh": self.thresh})
        ds.attrs = {**self.attrs, "schema_version": CLIMATOLOGY_SCHEMA_VERSION}
        chunks = {
            "dayofyear": min(183, DOY_LEN),
            "latitude": min(256, ds.sizes["latitude"]),
            "longitude": min(256, ds.sizes["longitude"]),
        }
        ds = ds.chunk(chunks)
        compressor = Blosc(cname="lz4", clevel=5, shuffle=Blosc.SHUFFLE)
        encoding = {v: {"compressor": compressor} for v in ds.data_vars}
        ds.to_zarr(str(path), mode="w", consolidated=True, encoding=encoding)
        logger.info(
            "Climatology → %s  (dayofyear=%d × lat=%d × lon=%d)",
            path,
            ds.sizes["dayofyear"],
            ds.sizes["latitude"],
            ds.sizes["longitude"],
        )

    # ------------------------------------------------------------------
    def expand_to_cube(
        self, times: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Broadcast (seas, thresh) to match an arbitrary time axis.

        Args:
            times: 1-D datetime64 array.

        Returns:
            Tuple of (seas, thresh) arrays, each of shape
            ``(len(times), latitude, longitude)``.
        """
        idx = _doy_index(times)
        seas = self.seas.isel(dayofyear=idx).values
        thresh = self.thresh.isel(dayofyear=idx).values
        return seas, thresh

    def expand_point(
        self, times: np.ndarray, lat: float, lon: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Nearest-neighbor (seas, thresh) for one (lat, lon) across `times`."""
        pt_seas = (
            self.seas.sel(latitude=lat, longitude=lon, method="nearest").values
        )
        pt_thresh = (
            self.thresh.sel(latitude=lat, longitude=lon, method="nearest").values
        )
        idx = _doy_index(times)
        return pt_seas[idx], pt_thresh[idx]

    def slice_bbox(
        self, bbox: tuple[float, float, float, float]
    ) -> Climatology:
        """Return a spatial subset. Coords are inclusive slices."""
        lon_min, lat_min, lon_max, lat_max = bbox
        # Build slices that work regardless of coord ordering.
        lat_slice = (
            slice(lat_min, lat_max)
            if float(self.seas["latitude"][0]) <= float(self.seas["latitude"][-1])
            else slice(lat_max, lat_min)
        )
        lon_slice = slice(lon_min, lon_max)
        seas = self.seas.sel(latitude=lat_slice, longitude=lon_slice)
        thresh = self.thresh.sel(latitude=lat_slice, longitude=lon_slice)
        return Climatology(seas=seas, thresh=thresh, attrs=dict(self.attrs))

    def fingerprint(self) -> str:
        """Return a deterministic SHA-256 hex digest of this climatology.

        The digest is computed over, in order:

        * ``seas.values.tobytes()``  — the float32 seasonal-mean array,
          in C-contiguous, native byte order. ``numpy``'s ``tobytes()`` is
          deterministic for a given array shape, dtype, and value layout.
        * ``thresh.values.tobytes()`` — same, for the percentile threshold.
        * The attrs dict serialized as canonical JSON (``sort_keys=True``,
          no whitespace), with ``created_utc`` excluded — that field records
          wall-clock at build time and is intentionally volatile.

        Determinism contract:
            Two ``Climatology`` instances built from the same inputs
            ``(source_dataset, clim_start, clim_end, bbox, window_half_width,
            smooth_width, pctile)`` MUST yield the same fingerprint. This is
            the canonical reproducibility identifier — reviewers can compare
            this hex against the reference value pinned in
            ``docs/reproducibility.md``.

        Note:
            The fingerprint depends on the in-memory float32 arrays, not on
            the zarr file bytes. This avoids spurious mismatches caused by
            blosc/lz4 compressor variations across platforms.
        """
        h = hashlib.sha256()
        # Force C-contiguous layout so tobytes() is reproducible regardless
        # of how upstream slicing produced this array.
        seas_bytes = np.ascontiguousarray(self.seas.values, dtype=np.float32).tobytes()
        thresh_bytes = np.ascontiguousarray(
            self.thresh.values, dtype=np.float32
        ).tobytes()
        h.update(seas_bytes)
        h.update(thresh_bytes)
        # Exclude wall-clock-at-build-time so two builds of the same inputs
        # tie out byte-for-byte at the fingerprint level.
        attrs_for_hash = {
            k: v for k, v in self.attrs.items() if k != "created_utc"
        }
        attrs_blob = json.dumps(
            attrs_for_hash, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        h.update(attrs_blob)
        return h.hexdigest()


def _doy_index(times: np.ndarray) -> np.ndarray:
    """Map a datetime64 array → 0-based DOY index into the 366-day baseline."""
    doy = pd.DatetimeIndex(times).dayofyear.to_numpy()
    doy = np.clip(doy, 1, DOY_LEN)
    return doy - 1


# ---------------------------------------------------------------------
# Climatology builder — used by the bootstrap script (not by request path).
# ---------------------------------------------------------------------
def build_climatology_from_cube(
    sst: xr.DataArray,
    clim_start: int,
    clim_end: int,
    window_half_width: int = 5,
    smooth_width: int = 31,
    pctile: float = 90.0,
    source_dataset: str = "",
    grid_resolution: str = "",
    bbox: tuple[float, float, float, float] | None = None,
) -> Climatology:
    """Compute per-DOY seasonal mean + percentile threshold from a raw SST cube.

    Semantics match ``marineHeatWaves.detect()`` so downstream event detection
    agrees with the reference implementation:

    * ±``window_half_width`` DOY pooling with wrap-around on a 366-day year.
    * Feb 29 gap filled by interpolating DOY 59 (Feb 28) and DOY 61 (Mar 1)
      when the pool is empty (rare — only happens if the reference period
      contains zero leap years).
    * ``smooth_width``-day periodic running mean over DOY.
    * ``pctile`` percentile with NaN-ignoring reduction (matches upstream
      ``percentile`` call with ``interpolation='linear'``).

    Args:
        sst: 3-D DataArray ``(time, latitude, longitude)`` in °C covering at
            least ``[clim_start, clim_end]``.
        clim_start, clim_end: inclusive year range to restrict the baseline.
        window_half_width: DOY pooling half-width (5 → 11-day window).
        smooth_width: running-mean width (31 is Hobday default).
        pctile: percentile for threshold, 90 in canonical MHW definition.
        source_dataset, grid_resolution, bbox: provenance recorded in attrs.

    Returns:
        A :class:`Climatology` ready to save.
    """
    if sst.ndim != 3:
        raise ValueError(f"Expected 3-D (time, lat, lon), got shape {sst.shape}")

    years = pd.DatetimeIndex(sst["time"].values).year
    year_mask = (years >= clim_start) & (years <= clim_end)
    if not year_mask.any():
        raise ValueError(
            f"No data in [{clim_start}, {clim_end}] in the provided cube"
        )
    sub = sst.isel(time=year_mask)
    doy = pd.DatetimeIndex(sub["time"].values).dayofyear.to_numpy()
    values = sub.values.astype("float32")  # (T, lat, lon)

    n_lat = sub.sizes["latitude"]
    n_lon = sub.sizes["longitude"]
    seas = np.full((DOY_LEN, n_lat, n_lon), np.nan, dtype="float32")
    thresh = np.full((DOY_LEN, n_lat, n_lon), np.nan, dtype="float32")

    for d in range(1, DOY_LEN + 1):
        window_days = np.arange(d - window_half_width, d + window_half_width + 1)
        window_days = ((window_days - 1) % DOY_LEN) + 1
        in_window = np.isin(doy, window_days)
        if not in_window.any():
            continue
        pool = values[in_window]
        with np.errstate(invalid="ignore", all="ignore"):
            seas[d - 1] = np.nanmean(pool, axis=0)
            thresh[d - 1] = np.nanpercentile(pool, pctile, axis=0)
        if d % 30 == 0:
            logger.debug("  built DOY %d/%d", d, DOY_LEN)

    # Feb 29 fallback: interpolate from Feb 28 and Mar 1 if empty.
    if np.isnan(seas[59]).all():
        seas[59] = 0.5 * (seas[58] + seas[60])
        thresh[59] = 0.5 * (thresh[58] + thresh[60])

    if smooth_width > 1:
        half = smooth_width // 2
        seas = _periodic_runmean(seas, half)
        thresh = _periodic_runmean(thresh, half)

    attrs: dict[str, Any] = {
        "clim_start": int(clim_start),
        "clim_end": int(clim_end),
        "window_half_width": int(window_half_width),
        "smooth_width": int(smooth_width),
        "pctile": float(pctile),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_dataset": source_dataset,
        "grid_resolution": grid_resolution,
    }
    if bbox is not None:
        attrs["bbox"] = list(bbox)

    return Climatology.from_arrays(
        seas=seas,
        thresh=thresh,
        latitudes=sub["latitude"].values,
        longitudes=sub["longitude"].values,
        attrs=attrs,
    )


def _periodic_runmean(arr: np.ndarray, half_width: int) -> np.ndarray:
    """Circular running mean along axis 0. NaN-aware (ignores NaNs within window)."""
    out = np.empty_like(arr)
    n = arr.shape[0]
    for i in range(n):
        idx = [(i + k) % n for k in range(-half_width, half_width + 1)]
        with np.errstate(invalid="ignore", all="ignore"):
            out[i] = np.nanmean(arr[idx], axis=0)
    return out
