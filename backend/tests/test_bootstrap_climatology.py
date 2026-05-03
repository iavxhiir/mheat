"""Unit tests for ``scripts/bootstrap_climatology.py``.

The bootstrap script is the operator-facing entry point that materialises the
30-year Hobday climatology zarr from the Copernicus Marine reanalysis. These
tests exercise its pure surface — argument parsing, time-coord coercion, year
clamping, the dry-run estimator, and the full happy path with the network
SDK calls mocked out — without performing any real download.

All ``copernicusmarine.subset`` and ``copernicusmarine.describe`` interactions
are stubbed via ``monkeypatch``: a real test invocation must never touch the
network.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

# scripts/ is not a package on disk (no __init__.py); make the repo root
# importable so ``import scripts.bootstrap_climatology`` works the same way
# the existing CLI shim (app/__main__.py) imports ``scripts.export_arco``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.bootstrap_climatology as bc  # noqa: E402
from scripts.bootstrap_climatology import _coerce_time as ct  # noqa: E402

from app.climatology import Climatology  # noqa: E402


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _no_network_subset(*args, **kwargs):
    """Drop-in for ``copernicusmarine.subset`` that fails the test if invoked."""
    pytest.fail(
        "copernicusmarine.subset was called during a test that must not "
        "perform any network I/O"
    )


def _stub_describe(coverage_min: str, coverage_max: str, lat_step: float = 0.0417,
                   lon_step: float = 0.0417):
    """Return a callable suitable for ``monkeypatch.setattr(bc, '_describe_dataset', ...)``."""
    def _impl(_dataset_id: str) -> dict:
        return {
            "tmin": coverage_min,
            "tmax": coverage_max,
            "lat_step": lat_step,
            "lon_step": lon_step,
        }
    return _impl


def _write_synthetic_nc(path: Path, *, n_days: int = 3, n_lat: int = 5,
                        n_lon: int = 5, start: str = "2020-06-01",
                        depth: float = 1.02) -> None:
    """Write a tiny ``thetao`` NetCDF mimicking the Copernicus Med 4.2 km layout.

    Mirrors the dim layout that ``_select_surface`` expects: one ``depth`` axis
    near ``depth_target`` plus the standard (time, latitude, longitude) trio.
    """
    times = pd.date_range(start, periods=n_days, freq="D")
    lats = np.linspace(40.0, 41.0, n_lat, dtype="float32")
    lons = np.linspace(10.0, 11.0, n_lon, dtype="float32")
    depths = np.array([depth], dtype="float32")
    data = np.full((n_days, 1, n_lat, n_lon), 20.0, dtype="float32")
    ds = xr.Dataset(
        {"thetao": (("time", "depth", "latitude", "longitude"), data)},
        coords={"time": times, "depth": depths,
                "latitude": lats, "longitude": lons},
        attrs={"source": "synthetic-test"},
    )
    ds["thetao"].attrs["units"] = "degree_Celsius"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, engine="h5netcdf")


# ---------------------------------------------------------------------
# 1. build_parser defaults
# ---------------------------------------------------------------------
def test_build_parser_defaults_match_documented() -> None:
    """The CLI defaults must match the documented bootstrap baseline."""
    parser = bc.build_parser()
    args = parser.parse_args([])

    # 30-year baseline window per Hobday convention; matches script constants.
    assert args.clim_start == 1993
    assert args.clim_end == 2019

    # Default Mediterranean bbox: matches backend/app/config.py.
    bbox = tuple(float(x) for x in args.bbox.split(","))
    assert bbox == (-6.0, 30.0, 36.5, 46.0)

    # Default catalogue product is the Med reanalysis SST.
    assert args.dataset_id.startswith("cmems_mod_med_phy-temp")

    # Surface depth on the Med 4.2 km vertical grid.
    assert args.depth_target == pytest.approx(1.02)

    # Output zarr lives under data/ by default.
    assert isinstance(args.output, Path)
    assert args.output.name == "climatology.zarr"


# ---------------------------------------------------------------------
# 2. dry-run prints estimate and exits zero, no network
# ---------------------------------------------------------------------
def test_dry_run_prints_estimate_and_exits_zero(monkeypatch, capsys, caplog) -> None:
    """``--dry-run`` must produce the size estimate but never invoke ``subset``."""
    monkeypatch.setattr(
        bc, "_describe_dataset",
        _stub_describe("1991-01-01", "2020-12-31",
                       lat_step=0.0417, lon_step=0.0417),
    )
    # Calling the SDK in a dry run is a contract violation — fail loudly.
    monkeypatch.setattr(bc.copernicusmarine, "subset", _no_network_subset)

    caplog.set_level("INFO", logger="bootstrap_climatology")
    code = bc.main([
        "--dry-run",
        "--clim-start", "2018",
        "--clim-end", "2018",
        "--bbox", "10,40,12,42",
    ])

    assert code == 0
    log_text = caplog.text
    # Operator-facing log lines we promise to emit.
    assert "Querying catalogue" in log_text
    assert "Years: 2018 -> 2018" in log_text
    # Estimated size is reported in MB float32.
    assert "MB float32" in log_text
    # Sanity: an MB number is present in the estimate line.
    estimate_lines = [ln for ln in log_text.splitlines() if "MB float32" in ln]
    assert estimate_lines, "expected at least one estimate log line"
    assert any(c.isdigit() for c in estimate_lines[0])


# ---------------------------------------------------------------------
# 3. _coerce_time helper
# ---------------------------------------------------------------------
def test_coerce_time_handles_iso_string_and_epoch_ms() -> None:
    """The helper must preserve time information from each catalogue scalar shape."""
    # ISO string: not parseable as a float, falls through to ``str(value)``.
    iso_out = ct("1991-01-01T00:00:00Z")
    assert iso_out is not None
    assert "1991" in str(iso_out), f"ISO coercion lost year: {iso_out!r}"

    # Epoch ms float ≈ 2026: 1774915200000 ms = 2026-03-31 UTC.
    ms_out = ct(1774915200000.0)
    assert ms_out is not None
    assert "2026" in str(ms_out), f"Epoch ms coercion lost year: {ms_out!r}"

    # Existing datetime object: not parseable as a float, falls through to
    # ``str(value)``. The contract is that the year info must survive — the
    # current implementation actually returns ``str(datetime)`` rather than
    # the datetime unchanged, but the year payload is preserved.
    existing = dt.datetime(2024, 7, 15, 12, 0, 0)
    dt_out = ct(existing)
    assert dt_out is not None
    assert "2024" in str(dt_out), f"datetime coercion lost year: {dt_out!r}"

    # None passes through.
    assert ct(None) is None


# ---------------------------------------------------------------------
# 4. year clamping when dataset ends mid-year
# ---------------------------------------------------------------------
def test_year_clamping_when_dataset_ends_mid_year(monkeypatch, capsys, caplog) -> None:
    """A dataset that ends in mid-2020 must clamp clim_end to 2019."""
    monkeypatch.setattr(
        bc, "_describe_dataset",
        _stub_describe("1991-01-01", "2020-06-02"),
    )
    monkeypatch.setattr(bc.copernicusmarine, "subset", _no_network_subset)

    caplog.set_level("WARNING", logger="bootstrap_climatology")
    # capture INFO too so we can read the post-clamp Years line.
    caplog.set_level("INFO", logger="bootstrap_climatology")

    code = bc.main([
        "--dry-run",
        "--clim-start", "1991",
        "--clim-end", "2020",
    ])
    assert code == 0

    log_text = caplog.text
    # Operator must see *why* the requested end was reduced.
    assert "clamping" in log_text.lower()
    assert "2020-06-02" in log_text
    # And the post-clamp window the run will actually use:
    assert "Years: 1991 -> 2019" in log_text


# ---------------------------------------------------------------------
# 5. invalid year range
# ---------------------------------------------------------------------
def test_main_returns_nonzero_on_invalid_year_range(monkeypatch, caplog) -> None:
    """``clim_start > clim_end`` must exit non-zero before any catalogue call."""
    # Guard: even if the script were to call describe/subset, fail the test —
    # the year-range check is meant to short-circuit before any network I/O.
    def _explode(*args, **kwargs):
        pytest.fail("describe must not be called when clim_start > clim_end")
    monkeypatch.setattr(bc, "_describe_dataset", _explode)
    monkeypatch.setattr(bc.copernicusmarine, "subset", _no_network_subset)

    caplog.set_level("ERROR", logger="bootstrap_climatology")
    code = bc.main(["--dry-run", "--clim-start", "2020", "--clim-end", "2019"])

    assert code != 0
    assert "clim_start" in caplog.text
    assert "clim_end" in caplog.text


# ---------------------------------------------------------------------
# 6. full run with synthetic cube and mocked subset
# ---------------------------------------------------------------------
def test_full_run_with_synthetic_cube_and_mocked_subset(
    monkeypatch, tmp_path, caplog
) -> None:
    """Happy path: stubbed ``subset`` writes a tiny NC, the rest is real code.

    Verifies that the bootstrap pipeline (subset → open → depth-select →
    build_climatology_from_cube → save zarr) survives end-to-end with a
    synthetic 3-day cube — and that the resulting zarr advertises the
    provenance attrs the runtime depends on.
    """
    # Coverage extends into mid-2021 so that ``_clamp_clim_end`` treats 2020
    # as a complete year (its last-complete-year heuristic is ``actual-1``).
    monkeypatch.setattr(
        bc, "_describe_dataset",
        _stub_describe("2020-01-01", "2021-06-01"),
    )

    download_dir = tmp_path / "downloads"
    out_zarr = tmp_path / "clim.zarr"
    dataset_id = "cmems_mod_med_phy-temp_my_4.2km_P1D-m"

    # Replace the network ``subset`` with a writer that drops a synthetic NC
    # at output_directory/output_filename — exactly where the script reads
    # from after the call returns.
    captured: dict[str, object] = {}

    def _fake_subset(**kwargs):
        captured.update(kwargs)
        out_dir = Path(kwargs["output_directory"])
        out_name = kwargs["output_filename"]
        _write_synthetic_nc(
            out_dir / out_name,
            n_days=3,
            n_lat=5,
            n_lon=5,
            start="2020-06-01",
            depth=kwargs["minimum_depth"] + 0.005,  # halfway between min/max
        )

    monkeypatch.setattr(bc.copernicusmarine, "subset", _fake_subset)

    caplog.set_level("INFO", logger="bootstrap_climatology")
    code = bc.main([
        "--clim-start", "2020",
        "--clim-end", "2020",
        "--bbox", "10,40,11,41",
        "--dataset-id", dataset_id,
        "--output", str(out_zarr),
        "--download-dir", str(download_dir),
    ])
    assert code == 0, f"main returned {code}; logs:\n{caplog.text}"

    # The mocked subset was actually invoked with the right wiring.
    assert captured["dataset_id"] == dataset_id
    assert captured["variables"] == ["thetao"]

    # Output zarr exists and is openable through the runtime container.
    assert out_zarr.exists(), "expected climatology zarr to be written"
    clim = Climatology.open(out_zarr)

    # Provenance attrs the live endpoints rely on:
    assert clim.attrs["clim_start"] == 2020
    assert clim.attrs["clim_end"] == 2020
    assert clim.attrs["source_dataset"] == dataset_id

    # Shape contract: per-DOY × spatial.
    assert clim.seas.shape == (366, 5, 5)
    assert clim.thresh.shape == (366, 5, 5)

    # Where coverage exists, the values must be finite (no infinities).
    seas = clim.seas.values
    thresh = clim.thresh.values
    assert np.isfinite(seas[~np.isnan(seas)]).all()
    assert np.isfinite(thresh[~np.isnan(thresh)]).all()
    # And at least *some* DOYs must have been filled in by the smoothing.
    assert np.isfinite(seas).any()
    assert np.isfinite(thresh).any()
