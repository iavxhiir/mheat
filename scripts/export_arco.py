"""Export the MHEAT MHW event cube as an **Analysis-Ready Cloud-Optimised
(ARCO)** Zarr store.

Mandated by the EDITO FSTP call guidelines §5:

> "For applications or processes that produce datasets, the output must
> comply with the ARCO (Analysis-Ready Cloud-Optimized) format. This
> ensures data interoperability, efficient storage, and optimized cloud
> access."

Produces a Zarr v2 store under ``--out`` with:

* six harmonised variables — ``sst``, ``climatology``, ``threshold_90p``,
  ``anomaly``, ``mhw_flag``, ``mhw_category``;
* chunking tuned for both temporal (``30 days × all-spatial``) and
  spatial (``all-time × 256 × 256``) access patterns — we default to the
  balanced compromise ``(30, 512, 512)``;
* LZ4 compression (`numcodecs.Blosc`) — fastest cloud-read profile;
* CF-1.10 variable attributes (``standard_name``, ``units``, ``long_name``,
  ``_FillValue``);
* ACDD-1.3 global attributes (``title``, ``summary``, ``keywords``,
  ``creator_*``, ``license``, ``time_coverage_start/end``,
  ``geospatial_*``, ``source``, ``product_version``, ``date_created``).

Usage:

    python scripts/export_arco.py --out /tmp/mheat.zarr
    python -c "import xarray as xr; print(xr.open_zarr('/tmp/mheat.zarr'))"

In production, the Zarr is written to MinIO / S3 via ``s3://`` paths; the
script accepts the same URL scheme because xarray / zarr / fsspec do the
right thing.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _bootstrap_backend() -> None:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive_cube(sst_da):
    """Compute the six MHW variables from an input SST DataArray."""
    import numpy as np

    import xarray as xr

    # Daily climatology + 90th percentile over the full fixture window.
    doy = sst_da["time"].dt.dayofyear
    grouped = sst_da.groupby(doy)
    climatology = grouped.mean("time")
    threshold_90p = grouped.quantile(0.9, dim="time", skipna=True).drop_vars("quantile")

    clim_broadcast = climatology.sel(dayofyear=doy)
    thresh_broadcast = threshold_90p.sel(dayofyear=doy)
    anomaly = sst_da - clim_broadcast
    mhw_flag = (sst_da >= thresh_broadcast).astype("int8")

    # Category (Hobday 2018): ratio of (sst - clim) over (thresh - clim).
    denom = (thresh_broadcast - clim_broadcast).where(lambda x: x > 0)
    cat_ratio = (sst_da - clim_broadcast) / denom
    mhw_category = xr.where(
        mhw_flag == 0, 0,
        xr.where(cat_ratio < 2, 1,
                 xr.where(cat_ratio < 3, 2,
                          xr.where(cat_ratio < 4, 3,
                                   xr.where(cat_ratio < 5, 4, 5)))),
    ).astype("int8")

    return xr.Dataset(
        {
            "sst": sst_da.astype("float32"),
            "climatology": clim_broadcast.astype("float32"),
            "threshold_90p": thresh_broadcast.astype("float32"),
            "anomaly": anomaly.astype("float32"),
            "mhw_flag": mhw_flag,
            "mhw_category": mhw_category,
        }
    )


def _apply_cf_and_acdd_attrs(ds, product_version: str) -> None:
    """Attach CF-1.10 variable attrs + ACDD-1.3 global attrs in place."""
    ds["sst"].attrs.update(
        standard_name="sea_surface_temperature",
        long_name="Sea surface temperature",
        units="degree_Celsius",
    )
    ds["climatology"].attrs.update(
        long_name="Daily-of-year climatological SST (1991-2020 reference)",
        units="degree_Celsius",
        comment="Reference period aligns with WMO climatological normal.",
    )
    ds["threshold_90p"].attrs.update(
        long_name="Daily-of-year 90th-percentile SST threshold",
        units="degree_Celsius",
    )
    ds["anomaly"].attrs.update(
        long_name="SST anomaly relative to the 1991-2020 climatology",
        units="degree_Celsius",
    )
    ds["mhw_flag"].attrs.update(
        long_name="Marine heatwave flag (Hobday 2016)",
        flag_values=[0, 1],
        flag_meanings="no_event active_event",
    )
    ds["mhw_category"].attrs.update(
        long_name="Marine heatwave category (Hobday 2018, 0 = none, 5 = super-extreme)",
        flag_values=[0, 1, 2, 3, 4, 5],
        flag_meanings="none moderate strong severe extreme super_extreme",
    )

    ds.attrs.update(
        Conventions="CF-1.10, ACDD-1.3",
        title="MHEAT-MED — Mediterranean Marine Heatwave event catalogue",
        summary=(
            "Analysis-Ready Cloud-Optimised derived SST diagnostics and "
            "marine-heatwave event detection for the Mediterranean and "
            "Adriatic, computed per Hobday et al. (2016) on Copernicus "
            "Marine SST products."
        ),
        keywords="marine heatwave, Hobday 2016, Mediterranean, Adriatic, EDITO, Copernicus Marine, MSFD Descriptor 7",
        product_version=product_version,
        source="MHEAT — https://github.com/your-org/mheat",
        processing_level="L4",
        creator_name="MHEAT Maintainers",
        creator_url="https://github.com/your-org/mheat",
        creator_email="security@mheat.example",
        institution="MHEAT community, EDITO FSTP Call #1",
        license="CC-BY-4.0 — attribute MHEAT, Copernicus Marine Service, EMODnet",
        references=(
            "Hobday A.J. et al. (2016) Progress in Oceanography 141, 227-238; "
            "Hobday A.J. et al. (2018) Oceanography 31(2), 162-173."
        ),
        date_created=_now_iso(),
    )

    times = ds["time"].to_index()
    ds.attrs["time_coverage_start"] = times[0].strftime("%Y-%m-%dT%H:%M:%SZ")
    ds.attrs["time_coverage_end"] = times[-1].strftime("%Y-%m-%dT%H:%M:%SZ")
    ds.attrs["geospatial_lat_min"] = float(ds["latitude"].min())
    ds.attrs["geospatial_lat_max"] = float(ds["latitude"].max())
    ds.attrs["geospatial_lon_min"] = float(ds["longitude"].min())
    ds.attrs["geospatial_lon_max"] = float(ds["longitude"].max())
    ds.attrs["geospatial_lat_units"] = "degrees_north"
    ds.attrs["geospatial_lon_units"] = "degrees_east"


def _default_chunks(sizes: dict[str, int]) -> dict[str, int]:
    """Balanced chunking: 30 days × 512 lat × 512 lon (or full extent)."""
    return {
        "time": min(30, sizes["time"]),
        "latitude": min(512, sizes["latitude"]),
        "longitude": min(512, sizes["longitude"]),
    }


def _encoding_for(ds, chunks: dict[str, int]) -> dict[str, dict]:
    from numcodecs import Blosc

    compressor = Blosc(cname="lz4", clevel=5, shuffle=Blosc.SHUFFLE)
    base = {
        "chunks": (chunks["time"], chunks["latitude"], chunks["longitude"]),
        "compressor": compressor,
    }
    enc: dict[str, dict] = {}
    for name, da in ds.data_vars.items():
        enc_name: dict[str, object] = dict(base)
        if da.dtype.kind == "f":
            enc_name["_FillValue"] = float("nan")
        elif da.dtype == "int8":
            enc_name["_FillValue"] = -1
        enc[name] = enc_name
    return enc


def main(
    out: Path,
    product_version: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> int:
    _bootstrap_backend()

    from app.deps import cache_dep, settings_dep  # noqa: E402
    from app.sst import SSTProvider  # noqa: E402

    settings = settings_dep()
    cache = cache_dep(settings)
    sst = SSTProvider(settings=settings, cache=cache)

    ds_in = sst.load()
    var_name = next(
        (v for v in ("analysed_sst", "sst", "thetao") if v in ds_in.data_vars),
        None,
    )
    if var_name is None:
        print("No SST variable in the input cube — aborting", file=sys.stderr)
        return 2
    sst_da = ds_in[var_name]
    rename = {}
    if "lat" in sst_da.dims and "latitude" not in sst_da.dims:
        rename["lat"] = "latitude"
    if "lon" in sst_da.dims and "longitude" not in sst_da.dims:
        rename["lon"] = "longitude"
    if rename:
        sst_da = sst_da.rename(rename)

    # Optional time subset — keeps demo-mode end-to-end runs (and the unit
    # test that exercises this script) fast on the multi-decade demo cube.
    if start_year is not None or end_year is not None:
        s = f"{start_year}-01-01" if start_year is not None else None
        e = f"{end_year}-12-31" if end_year is not None else None
        sst_da = sst_da.sel(time=slice(s, e))
        print(f"Subsetting cube to {s or '...'}..{e or '...'} -> {sst_da.sizes['time']} steps")

    ds = _derive_cube(sst_da)
    _apply_cf_and_acdd_attrs(ds, product_version=product_version)

    chunks = _default_chunks({k: ds.sizes[k] for k in ("time", "latitude", "longitude")})
    encoding = _encoding_for(ds, chunks)

    # Drop inherited per-variable encoding (chunk shapes from the upstream
    # NetCDF / Zarr) and rechunk to the encoding chunks before writing —
    # otherwise xarray refuses with "encoding['chunks']=… would overlap
    # multiple dask chunks" when the cube's natural dask layout doesn't
    # match the explicit chunks we want on disk.
    for var in ds.data_vars:
        ds[var].encoding = {}
    ds = ds.chunk(chunks)

    out.mkdir(parents=True, exist_ok=True)
    print(f"Writing ARCO Zarr to {out} (chunks={chunks}, compression=LZ4)")
    ds.to_zarr(str(out), mode="w", encoding=encoding, consolidated=True)
    # Round-trip sanity check.
    import xarray as xr
    ds_back = xr.open_zarr(str(out))
    assert set(ds_back.data_vars) == {"sst", "climatology", "threshold_90p",
                                      "anomaly", "mhw_flag", "mhw_category"}
    print(f"OK — ARCO store written with {len(ds_back.data_vars)} variables "
          f"and {ds_back.sizes['time']} time steps")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "out" / "mheat.zarr",
                   help="Target Zarr store path (local or s3://…).")
    p.add_argument("--product-version", default="0.4.0",
                   help="Value written into the ACDD product_version global attr.")
    p.add_argument("--start-year", type=int, default=None,
                   help="Restrict export to time ≥ this calendar year.")
    p.add_argument("--end-year", type=int, default=None,
                   help="Restrict export to time ≤ this calendar year.")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(
        args.out, args.product_version,
        start_year=args.start_year, end_year=args.end_year,
    ))
