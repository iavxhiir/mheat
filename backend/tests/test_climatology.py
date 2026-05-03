"""Unit tests for the pre-computed Hobday climatology artifact.

These tests exercise the public surface of ``app.climatology`` — the
:class:`Climatology` dataclass (round-trip persistence, DOY indexing,
nearest-neighbor expansion, bbox slicing) plus the
:func:`build_climatology_from_cube` builder.

The motivation is to lock down the bootstrap-time invariants so that the
30-year reduction we pre-compute and ship (instead of recomputing on every
request) keeps producing values that downstream MHW detection trusts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app.climatology import (
    DOY_LEN,
    Climatology,
    build_climatology_from_cube,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _zeros_clim(n_lat: int = 3, n_lon: int = 3) -> Climatology:
    """A trivial all-zero baseline of the requested grid size."""
    seas = np.zeros((DOY_LEN, n_lat, n_lon), dtype="float32")
    thresh = np.zeros((DOY_LEN, n_lat, n_lon), dtype="float32")
    lats = np.linspace(40.0, 41.0, n_lat, dtype="float32")
    lons = np.linspace(10.0, 11.0, n_lon, dtype="float32")
    return Climatology.from_arrays(seas, thresh, lats, lons, attrs={"note": "test"})


def _synthetic_cube(
    n_years: int,
    *,
    seas_fn=lambda doy: 20.0,
    n_lat: int = 2,
    n_lon: int = 2,
    start: str = "2020-01-01",
) -> xr.DataArray:
    """Build a small (time, lat, lon) cube with a per-DOY seasonal signal.

    ``seas_fn(doy)`` is broadcast spatially, so every pixel sees the same
    deterministic time series — this isolates the climatology builder from
    spatial heterogeneity in the assertions below.
    """
    times = pd.date_range(start, periods=365 * n_years + (n_years // 4), freq="D")
    doy = np.array([t.dayofyear for t in times])
    series = np.array([seas_fn(int(d)) for d in doy], dtype="float32")
    cube = np.broadcast_to(series[:, None, None], (len(times), n_lat, n_lon)).copy()
    lats = np.linspace(40.0, 40.5, n_lat, dtype="float32")
    lons = np.linspace(10.0, 10.5, n_lon, dtype="float32")
    return xr.DataArray(
        cube,
        dims=("time", "latitude", "longitude"),
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


# ---------------------------------------------------------------------
# Climatology container
# ---------------------------------------------------------------------
def test_from_arrays_validates_shape() -> None:
    """Shape mismatch must surface as a ValueError, not a silent broadcast."""
    bad = np.zeros((DOY_LEN, 2, 2), dtype="float32")
    lats = np.array([40.0, 41.0, 42.0], dtype="float32")  # 3, not 2
    lons = np.array([10.0, 11.0], dtype="float32")
    with pytest.raises(ValueError, match="seas shape"):
        Climatology.from_arrays(bad, bad, lats, lons)


def test_save_and_open_roundtrip(tmp_path) -> None:
    """A built artifact must round-trip through zarr without value drift."""
    rng = np.random.default_rng(42)
    seas = rng.uniform(15, 25, size=(DOY_LEN, 2, 3)).astype("float32")
    thresh = seas + 2.0
    lats = np.array([40.0, 41.0], dtype="float32")
    lons = np.array([10.0, 10.5, 11.0], dtype="float32")
    clim = Climatology.from_arrays(seas, thresh, lats, lons, attrs={"src": "test"})
    out = tmp_path / "clim.zarr"
    clim.save(out)

    reloaded = Climatology.open(out)
    np.testing.assert_allclose(reloaded.seas.values, seas, rtol=0, atol=0)
    np.testing.assert_allclose(reloaded.thresh.values, thresh, rtol=0, atol=0)
    assert reloaded.attrs["src"] == "test"
    assert reloaded.attrs["schema_version"] == "1"


def test_expand_to_cube_matches_doy_indexing() -> None:
    """``expand_to_cube`` must DOY-index — the value for DOY n is at index n-1."""
    n_lat, n_lon = 2, 2
    seas = np.zeros((DOY_LEN, n_lat, n_lon), dtype="float32")
    thresh = np.zeros_like(seas)
    # Tag each DOY with its 0-based index so we can verify routing.
    for i in range(DOY_LEN):
        seas[i, :, :] = i
        thresh[i, :, :] = i + 1000  # distinct from seas
    lats = np.array([40.0, 41.0], dtype="float32")
    lons = np.array([10.0, 11.0], dtype="float32")
    clim = Climatology.from_arrays(seas, thresh, lats, lons)

    times = pd.date_range("2022-01-01", periods=5, freq="D").values  # DOY 1..5
    s, t = clim.expand_to_cube(times)
    assert s.shape == (5, n_lat, n_lon)
    # DOY 1 → index 0, DOY 5 → index 4: values must equal index.
    for n in range(5):
        assert np.all(s[n] == n)
        assert np.all(t[n] == n + 1000)


def test_expand_point_nearest_neighbor() -> None:
    """A non-grid (lat, lon) must snap to the nearest cell, not interpolate."""
    seas = np.zeros((DOY_LEN, 3, 3), dtype="float32")
    thresh = np.zeros_like(seas)
    lats = np.array([40.0, 41.0, 42.0], dtype="float32")
    lons = np.array([10.0, 11.0, 12.0], dtype="float32")
    # Tag the centre cell uniquely so we can detect a successful NN snap.
    seas[:, 1, 1] = 99.0
    thresh[:, 1, 1] = 88.0
    clim = Climatology.from_arrays(seas, thresh, lats, lons)

    # (40.9, 11.05) → nearest cell is (41.0, 11.0) → value 99 / 88.
    times = pd.date_range("2022-06-01", periods=3, freq="D").values
    pt_seas, pt_thresh = clim.expand_point(times, lat=40.9, lon=11.05)
    assert pt_seas.shape == (3,)
    np.testing.assert_array_equal(pt_seas, [99.0, 99.0, 99.0])
    np.testing.assert_array_equal(pt_thresh, [88.0, 88.0, 88.0])


def test_slice_bbox_trims_grid() -> None:
    """A bbox slice should reduce the spatial extent while preserving DOY."""
    n = 10
    seas = np.zeros((DOY_LEN, n, n), dtype="float32")
    thresh = np.zeros_like(seas)
    lats = np.linspace(40.0, 49.0, n, dtype="float32")
    lons = np.linspace(10.0, 19.0, n, dtype="float32")
    clim = Climatology.from_arrays(seas, thresh, lats, lons)

    sub = clim.slice_bbox((12.0, 42.0, 16.0, 46.0))
    # 5x5 cells (indices 2..6 inclusive on each axis).
    assert sub.seas.sizes["latitude"] == 5
    assert sub.seas.sizes["longitude"] == 5
    assert sub.seas.sizes["dayofyear"] == DOY_LEN
    # Coordinate values must lie inside the requested bbox.
    assert float(sub.seas["latitude"].min()) >= 42.0
    assert float(sub.seas["latitude"].max()) <= 46.0
    assert float(sub.seas["longitude"].min()) >= 12.0
    assert float(sub.seas["longitude"].max()) <= 16.0


# ---------------------------------------------------------------------
# build_climatology_from_cube
# ---------------------------------------------------------------------
def test_build_climatology_reproduces_uniform_input() -> None:
    """Constant SST in → constant seas/thresh out (sanity floor)."""
    cube = _synthetic_cube(n_years=3, seas_fn=lambda doy: 20.0)
    clim = build_climatology_from_cube(cube, clim_start=2020, clim_end=2022)
    assert clim.seas.shape == (DOY_LEN, 2, 2)
    # Every DOY should be ≈ 20°C (allow tiny float32 noise).
    np.testing.assert_allclose(clim.seas.values, 20.0, atol=1e-3)
    np.testing.assert_allclose(clim.thresh.values, 20.0, atol=1e-3)


def test_build_climatology_injects_seasonality() -> None:
    """A pure cosine seasonal cycle must be reconstructed in amplitude + phase.

    Tolerance is ±0.5°C per the rubric: the 11-day pool + 31-day smooth blurs
    the peaks slightly, but should not shift the phase or scale the amplitude.
    """
    def seas_fn(doy: int) -> float:
        return 15.0 + 5.0 * np.cos((doy - 15) * 2 * np.pi / 365.0)

    cube = _synthetic_cube(n_years=3, seas_fn=seas_fn)
    clim = build_climatology_from_cube(cube, clim_start=2020, clim_end=2022)
    seas = clim.seas.values[:, 0, 0]  # any pixel — they're identical

    # Amplitude: peak-to-peak ≈ 10°C.
    amp = float(seas.max() - seas.min())
    assert 9.5 <= amp <= 10.5, f"amplitude {amp:.2f} outside [9.5, 10.5]"

    # Phase: max should land on DOY 15 ± a few days (smoothing blurs by ≤15d).
    peak_doy = int(np.argmax(seas)) + 1  # DOY is 1-based
    # The cos peaks at doy=15; smoothing across the 366-day boundary may shift.
    diff = min(abs(peak_doy - 15), 366 - abs(peak_doy - 15))
    assert diff <= 15, f"peak DOY {peak_doy} too far from 15 (diff={diff})"


def test_build_climatology_feb29_interpolated() -> None:
    """DOY 60 (Feb 29) must be filled — interpolated when no leap data exists.

    With 3 non-leap years (2021-2023), DOY 60 is never sampled directly, so
    the explicit Feb 28 ↔ Mar 1 interpolation branch in the builder must
    leave a finite, sensible value.
    """
    cube = _synthetic_cube(
        n_years=3,
        seas_fn=lambda doy: 20.0 + 0.05 * doy,  # monotonic ramp by DOY
        start="2021-01-01",  # 2021, 2022, 2023 are all non-leap
    )
    clim = build_climatology_from_cube(cube, clim_start=2021, clim_end=2023)
    seas = clim.seas.values[:, 0, 0]
    assert np.isfinite(seas[59]), "Feb 29 (DOY 60) must not be NaN"
    # Should be close to mean(DOY 59, DOY 61) — modulo smoothing.
    interp = 0.5 * (seas[58] + seas[60])
    assert abs(seas[59] - interp) < 0.6, (
        f"DOY 60 ({seas[59]:.3f}) not near mean of neighbours ({interp:.3f})"
    )


def test_build_climatology_raises_if_no_data_in_window() -> None:
    """Asking for a baseline period outside the cube must fail loudly."""
    cube = _synthetic_cube(n_years=3, start="2020-01-01")  # 2020-2022
    with pytest.raises(ValueError, match="No data"):
        build_climatology_from_cube(cube, clim_start=1990, clim_end=1995)
