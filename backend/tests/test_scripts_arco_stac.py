"""End-to-end smoke tests for the ARCO Zarr export and STAC registration
scripts. These are invoked by a reviewer to confirm the §5 "ARCO output"
and "STAC registration" claims without needing an EDITO account.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], **env_overrides) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, **env_overrides}
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=240, env=env,
    )


def test_export_arco_produces_valid_zarr_cube(tmp_path: Path) -> None:
    out = tmp_path / "mheat.zarr"
    # Subset to a 3-year window — the long demo cube (1982-2026) is too
    # heavy for a 4-minute test, and this test verifies the export pipeline
    # not the cube extent (CF/ACDD attrs, variable shape, round-trip).
    proc = _run(
        [sys.executable, "scripts/export_arco.py", "--out", str(out),
         "--start-year", "2020", "--end-year", "2022"],
        DEMO_MODE="true",
    )
    assert proc.returncode == 0, f"export_arco.py failed:\n{proc.stdout}\n{proc.stderr}"

    import xarray as xr

    ds = xr.open_zarr(str(out))
    assert set(ds.data_vars) == {
        "sst", "climatology", "threshold_90p", "anomaly", "mhw_flag", "mhw_category",
    }
    # ACDD / CF attrs must be populated.
    for attr in ("Conventions", "title", "license", "time_coverage_start",
                 "geospatial_lat_min", "product_version"):
        assert attr in ds.attrs, f"missing ACDD/CF attr: {attr}"
    assert "CF-1.10" in ds.attrs["Conventions"]
    # Variable-level CF attrs.
    assert ds["sst"].attrs["units"] == "degree_Celsius"
    assert "standard_name" in ds["sst"].attrs


def test_register_stac_dry_run_writes_valid_tree(tmp_path: Path) -> None:
    out = tmp_path / "stac"
    proc = _run(
        [sys.executable, "scripts/register_stac.py",
         "--out", str(out), "--years", "2022", "2023"],
    )
    assert proc.returncode == 0, f"register_stac.py failed:\n{proc.stdout}\n{proc.stderr}"
    assert "validates" in proc.stdout

    import pystac

    collection = pystac.Collection.from_file(str(out / "collection.json"))
    collection.validate()
    items = list(collection.get_items(recursive=True))
    assert len(items) == 2
    assert collection.license == "CC-BY-4.0"
    # Must cite Hobday 2016 + 2018 via the scientific extension.
    pubs = collection.extra_fields.get("sci:publications", [])
    dois = {p.get("doi") for p in pubs}
    assert "10.1016/j.pocean.2015.12.014" in dois
    assert "10.5670/oceanog.2018.205" in dois
