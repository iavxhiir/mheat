"""One-time bootstrap for the MHEAT live-mode Hobday climatology.

Live-mode endpoints (``/api/anomaly``, ``/api/events``, ``/api/detect``) cannot
recompute a 30-year per-DOY seasonal mean and 90th-percentile threshold inside
a single request — the reduction is multi-GB and minute-scale. This script
performs that reduction *once* against the Copernicus Marine Mediterranean
reanalysis (``cmems_mod_med_phy-temp_my_4.2km_P1D-m``) and writes the result
to ``data/climatology.zarr``. The runtime then opens that zarr in memory and
broadcasts it to whatever time axis the live cube needs.

Run from the ``mheat/`` project root::

    .venv/Scripts/python.exe scripts/bootstrap_climatology.py --dry-run
    .venv/Scripts/python.exe scripts/bootstrap_climatology.py  # ~5-20 GB download
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shutil
import sys
import time
from pathlib import Path

# The script lives at <repo>/scripts/. Climatology code lives in <repo>/backend/app.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import copernicusmarine  # noqa: E402
import xarray as xr  # noqa: E402

from app.climatology import build_climatology_from_cube  # noqa: E402

log = logging.getLogger("bootstrap_climatology")

DEFAULT_BBOX = (-6.0, 30.0, 36.5, 46.0)  # matches backend/app/config.py
DEFAULT_DATASET_ID = "cmems_mod_med_phy-temp_my_4.2km_P1D-m"
DEFAULT_VARIABLE = "thetao"
DEFAULT_DEPTH = 1.02  # surface level on the Med 4.2 km vertical grid (m)
DEFAULT_OUTPUT = "data/climatology.zarr"
DEFAULT_DOWNLOAD_DIR = "data/_bootstrap_cache"
DEFAULT_CLIM_START = 1993
DEFAULT_CLIM_END = 2019


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bootstrap_climatology",
        description="Bootstrap the per-DOY Hobday MHW climatology zarr.",
    )
    p.add_argument("--clim-start", type=int, default=DEFAULT_CLIM_START)
    p.add_argument("--clim-end", type=int, default=DEFAULT_CLIM_END)
    p.add_argument(
        "--bbox",
        default=",".join(str(x) for x in DEFAULT_BBOX),
        help='"lon_min,lat_min,lon_max,lat_max"',
    )
    p.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    p.add_argument("--variable", default=DEFAULT_VARIABLE)
    p.add_argument("--depth-target", type=float, default=DEFAULT_DEPTH)
    p.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    p.add_argument("--download-dir", type=Path, default=Path(DEFAULT_DOWNLOAD_DIR))
    p.add_argument("--cleanup-downloads", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO")
    return p


def _parse_bbox(s: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"--bbox must be 4 comma-separated floats, got {s!r}")
    lon_min, lat_min, lon_max, lat_max = parts
    if lon_min >= lon_max or lat_min >= lat_max:
        raise ValueError(f"degenerate bbox: {parts}")
    return (lon_min, lat_min, lon_max, lat_max)


def _coerce_time(value) -> str | None:
    """Catalogue time-coord scalars come as ISO strings or epoch milliseconds."""
    if value is None:
        return None
    try:
        ms = float(value)
        return dt.datetime.utcfromtimestamp(ms / 1000.0).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)


def _describe_dataset(dataset_id: str) -> dict:
    """Pull (tmin, tmax, lat_step, lon_step) from the catalogue in one call."""
    catalogue = copernicusmarine.describe(
        dataset_id=dataset_id, disable_progress_bar=True
    )
    info = {"tmin": None, "tmax": None, "lat_step": None, "lon_step": None}
    for product in catalogue.products:
        for ds in product.datasets:
            if ds.dataset_id != dataset_id:
                continue
            for part in ds.versions[0].parts:
                for service in part.services:
                    for variable in service.variables:
                        for c in variable.coordinates:
                            if c.coordinate_id == "time":
                                info["tmin"] = _coerce_time(c.minimum_value) or info["tmin"]
                                info["tmax"] = _coerce_time(c.maximum_value) or info["tmax"]
                            elif c.coordinate_id == "latitude" and c.step is not None:
                                info["lat_step"] = float(c.step)
                            elif c.coordinate_id == "longitude" and c.step is not None:
                                info["lon_step"] = float(c.step)
    return info


def _clamp_clim_end(clim_end: int, max_iso: str | None) -> int:
    """If dataset coverage stops before ``clim_end``, clamp to the last full year."""
    if not max_iso:
        return clim_end
    try:
        actual_year = int(max_iso[:4])
    except ValueError:
        return clim_end
    last_complete = actual_year - 1  # the dataset may end mid-year
    if clim_end > last_complete:
        log.warning(
            "Requested clim_end=%d exceeds dataset coverage (max=%s); clamping to %d.",
            clim_end, max_iso, last_complete,
        )
        return last_complete
    return clim_end


def _select_surface(ds: xr.Dataset, variable: str, depth_target: float) -> xr.DataArray:
    """Slice to the depth nearest ``depth_target`` and normalise to (time, latitude, longitude)."""
    da = ds[variable]
    depth_dim = next((d for d in ("depth", "deptht", "lev", "z") if d in da.dims), None)
    if depth_dim is not None:
        da = da.sel({depth_dim: depth_target}, method="nearest")
        log.info(
            "Depth nearest %.3f m → %.3f m (dim %r)",
            depth_target, float(da[depth_dim].values), depth_dim,
        )
        da = da.drop_vars(depth_dim, errors="ignore")
    da = da.squeeze(drop=True)
    rename = {src: dst for src, dst in
              (("lat", "latitude"), ("lon", "longitude"),
               ("nav_lat", "latitude"), ("nav_lon", "longitude"))
              if src in da.dims or src in da.coords}
    return da.rename(rename) if rename else da


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bbox = _parse_bbox(args.bbox)
    if args.clim_start > args.clim_end:
        log.error("clim_start (%d) > clim_end (%d)", args.clim_start, args.clim_end)
        return 2

    log.info("Querying catalogue for %s ...", args.dataset_id)
    try:
        info = _describe_dataset(args.dataset_id)
    except Exception as e:  # noqa: BLE001 — friendly error for the bootstrap operator
        log.error("Failed to describe dataset %s: %s", args.dataset_id, e)
        return 3
    log.info("Dataset coverage: %s -> %s", info["tmin"], info["tmax"])
    log.info("Native step (deg): lat=%s lon=%s", info["lat_step"], info["lon_step"])

    clim_end = _clamp_clim_end(args.clim_end, info["tmax"])
    if clim_end < args.clim_start:
        log.error("After clamping, clim_end %d < clim_start %d", clim_end, args.clim_start)
        return 2

    lon_min, lat_min, lon_max, lat_max = bbox
    n_days = int((clim_end - args.clim_start + 1) * 365.25)
    if info["lat_step"] and info["lon_step"]:
        n_lat = int((lat_max - lat_min) / info["lat_step"]) + 1
        n_lon = int((lon_max - lon_min) / info["lon_step"]) + 1
        approx_mb = n_days * n_lat * n_lon * 4 / (1024 * 1024)
    else:
        n_lat = n_lon = 0
        approx_mb = float("nan")
    log.info("Bbox: %s", bbox)
    log.info("Years: %d -> %d (%d days)", args.clim_start, clim_end, n_days)
    log.info(
        "Estimated subset shape: time=%d lat=%d lon=%d  ~ %.0f MB float32",
        n_days, n_lat, n_lon, approx_mb,
    )
    if args.dry_run:
        log.info("DRY RUN -- exiting before download.")
        return 0

    args.download_dir.mkdir(parents=True, exist_ok=True)
    nc_path = args.download_dir / "reanalysis.nc"
    log.info("Downloading to %s ...", nc_path)
    t0 = time.monotonic()
    copernicusmarine.subset(
        dataset_id=args.dataset_id,
        variables=[args.variable],
        minimum_longitude=lon_min,
        maximum_longitude=lon_max,
        minimum_latitude=lat_min,
        maximum_latitude=lat_max,
        minimum_depth=args.depth_target - 0.01,
        maximum_depth=args.depth_target + 0.01,
        start_datetime=f"{args.clim_start}-01-01T00:00:00",
        end_datetime=f"{clim_end}-12-31T23:59:59",
        output_directory=str(args.download_dir),
        output_filename="reanalysis.nc",
        overwrite=True,
    )
    log.info("Download finished in %.1fs.", time.monotonic() - t0)

    log.info("Opening %s ...", nc_path)
    ds = xr.open_dataset(nc_path, engine="h5netcdf")
    sst = _select_surface(ds, args.variable, args.depth_target)
    log.info("SST cube ready: dims=%s shape=%s", sst.dims, sst.shape)

    log.info("Building climatology (this can take several minutes) ...")
    t1 = time.monotonic()
    clim = build_climatology_from_cube(
        sst,
        clim_start=args.clim_start,
        clim_end=clim_end,
        source_dataset=args.dataset_id,
        grid_resolution="4.2km native",
        bbox=bbox,
    )
    log.info("Built in %.1fs.", time.monotonic() - t1)

    clim.save(args.output)
    log.info("Wrote %s (%.1f MB on disk).", args.output, _dir_size_mb(args.output))
    log.info("Attrs: %s", clim.attrs)
    log.info("Total elapsed: %.1fs.", time.monotonic() - t0)

    if args.cleanup_downloads:
        log.info("Removing raw cache %s ...", args.download_dir)
        shutil.rmtree(args.download_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
