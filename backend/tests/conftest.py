"""Pytest fixtures: pre-populate the cache + climatology, mock the CMS SDK.

The runtime is cache-first and live-only after pass 83 — there is no demo
mode and no synthetic-cube generator left in the app code. Tests therefore
need a *populated* substrate to exercise: a tiny SST Zarr cube, a tiny
Hobday climatology Zarr next to it, and a mocked ``copernicusmarine.subset``
that produces synthetic NetCDFs whenever the cache-miss path tries to fetch.

This conftest installs that substrate session-wide so every test file picks
it up by default. Per-test overrides go through ``monkeypatch`` /
``tmp_path`` / dependency overrides as usual.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import xarray as xr

# --------------------------------------------------------------------------
# Synthetic-substrate sizing.
#
# The grid is intentionally small so zarr writes/reads stay under a few
# hundred milliseconds. Latitudes are chosen so a request for nearest=40.5,
# 10.5 hits the centre of the grid; longitudes match.
# --------------------------------------------------------------------------
_LATS = np.linspace(40.0, 41.0, 5, dtype="float32")
_LONS = np.linspace(10.0, 11.0, 5, dtype="float32")
_CLIM_START_YEAR = 1991
_CLIM_END_YEAR = 2020
# Cube spans the last ~120 days so any test that asks for "today minus N"
# with N ≤ 120 is served from cache; longer windows trigger the lazy-fill
# code path which then hits our copernicusmarine.subset mock.
_CUBE_DAYS = 120


def _build_synthetic_sst_cube(end: date | None = None) -> xr.Dataset:
    """Build a (time × lat × lon) SST cube ending on ``end`` (default: today)."""
    end = end or date.today()
    times = pd.date_range(end=pd.Timestamp(end), periods=_CUBE_DAYS, freq="D")
    # Anomalously warm slab in the middle 10 days so MHW detection actually
    # finds at least one event during tests that exercise /api/events.
    base = np.full(
        (times.size, _LATS.size, _LONS.size), 21.0, dtype="float32",
    )
    mid = times.size // 2
    base[mid - 5: mid + 5, :, :] = 26.0
    return xr.Dataset(
        data_vars={
            "analysed_sst": (
                ("time", "latitude", "longitude"), base,
                {"units": "degC", "long_name": "Analysed SST"},
            ),
        },
        coords={
            "time": times,
            "latitude": _LATS,
            "longitude": _LONS,
        },
        attrs={
            "title": "MHEAT test fixture SST cube",
            "source": "tests/conftest.py",
        },
    )


def _build_synthetic_climatology() -> tuple[np.ndarray, np.ndarray]:
    """Return (seas, thresh) arrays of shape (366, lat, lon) in °C.

    Built so the synthetic SST values (21 °C baseline, 26 °C in the warm
    slab) cleanly cross the 90th-percentile threshold (set to 23 °C) for
    the ≥5-day duration the Hobday detector needs.
    """
    from app.climatology import DOY_LEN

    seas = np.full((DOY_LEN, _LATS.size, _LONS.size), 20.0, dtype="float32")
    thresh = np.full((DOY_LEN, _LATS.size, _LONS.size), 23.0, dtype="float32")
    return seas, thresh


def _write_synthetic_substrate(cache_dir: Path) -> None:
    """Materialise sst.zarr + climatology.zarr under ``cache_dir``."""
    from app.climatology import Climatology

    cube = _build_synthetic_sst_cube()
    sst_path = cache_dir / "sst.zarr"
    if sst_path.exists():
        import shutil
        shutil.rmtree(sst_path, ignore_errors=True)
    cube.to_zarr(str(sst_path), mode="w", consolidated=True)

    seas, thresh = _build_synthetic_climatology()
    clim = Climatology.from_arrays(
        seas=seas, thresh=thresh,
        latitudes=_LATS, longitudes=_LONS,
        attrs={
            "clim_start": _CLIM_START_YEAR,
            "clim_end": _CLIM_END_YEAR,
            "source_dataset": "synthetic-test-fixture",
            "grid_resolution": "0.25deg",
        },
    )
    clim.save(cache_dir / "climatology.zarr")


def _build_synthetic_subset_nc(
    out_path: Path,
    start: date,
    end: date,
    variable: str = "analysed_sst",
    needs_surface: bool = False,
) -> None:
    """Mimic what ``copernicusmarine.subset`` writes — adapts to the variable.

    NRT L4 ships ``analysed_sst`` (2-D, kelvin). Reanalysis and forecast ship
    ``thetao`` (3-D, depth × time × lat × lon, °C). The mock matches the
    variable name and dimensionality the caller requested so the
    ``_to_surface_sst`` / ``_ensure_celsius`` post-processing in app.sst
    runs unchanged.
    """
    times = pd.date_range(start, end, freq="D")
    n_days = max(times.size, 1)
    if variable == "analysed_sst":
        values = np.full(
            (n_days, _LATS.size, _LONS.size),
            25.0 + 273.15,
            dtype="float32",
        )
        attrs = {"units": "kelvin", "long_name": "Analysed SST"}
        coords = {
            "time": times if times.size > 0 else pd.DatetimeIndex([start]),
            "latitude": _LATS,
            "longitude": _LONS,
        }
        ds = xr.Dataset(
            data_vars={variable: (("time", "latitude", "longitude"), values, attrs)},
            coords=coords,
            attrs={"title": "MHEAT synthetic CMS NRT subset (test fixture)"},
        )
    else:
        # thetao with a depth axis. Surface-extract picks the level nearest
        # 1.02 m, so we expose two levels straddling that target.
        # Use 26 °C — well above the synthetic 23 °C threshold — so MHW
        # detection on lazy-filled reanalysis windows still finds events;
        # otherwise tests that ask for an old date range get an empty
        # FeatureCollection and assert n>=1 trips.
        depths = np.array([0.5, 1.0182], dtype="float32")
        values = np.full(
            (n_days, depths.size, _LATS.size, _LONS.size),
            26.0,
            dtype="float32",
        )
        ds = xr.Dataset(
            data_vars={
                variable: (
                    ("time", "depth", "latitude", "longitude"),
                    values,
                    {"units": "degC", "long_name": "Sea water potential temperature"},
                ),
            },
            coords={
                "time": times if times.size > 0 else pd.DatetimeIndex([start]),
                "depth": depths,
                "latitude": _LATS,
                "longitude": _LONS,
            },
            attrs={
                "title": "MHEAT synthetic CMS thetao subset (test fixture)",
            },
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path)


def _make_fake_copernicusmarine() -> types.ModuleType:
    """Build a stub ``copernicusmarine`` module that writes synthetic NetCDFs."""
    fake_mod = types.ModuleType("copernicusmarine")

    def subset(**kwargs: Any) -> None:
        out_dir = Path(kwargs["output_directory"])
        out_name = kwargs["output_filename"]
        start = date.fromisoformat(str(kwargs["start_datetime"])[:10])
        end = date.fromisoformat(str(kwargs["end_datetime"])[:10])
        # The provider passes a single-element ``variables`` list; respect it
        # so reanalysis/forecast paths get a thetao file and NRT gets an
        # analysed_sst file. ``minimum_depth`` only appears in 3-D requests.
        variables = kwargs.get("variables") or ["analysed_sst"]
        variable = variables[0]
        needs_surface = "minimum_depth" in kwargs
        _build_synthetic_subset_nc(
            out_dir / out_name, start, end,
            variable=variable, needs_surface=needs_surface,
        )

    fake_mod.subset = subset  # type: ignore[attr-defined]
    return fake_mod


# --------------------------------------------------------------------------
# Session-scoped fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _isolated_substrate() -> None:
    """Redirect cache + climatology to a per-session tempdir and pre-populate it."""
    tmp = Path(tempfile.mkdtemp(prefix="mheat-test-"))
    os.environ["CACHE_DIR"] = str(tmp)
    os.environ["ZARR_STORE"] = str(tmp / "sst.zarr")
    os.environ["CLIMATOLOGY_STORE"] = str(tmp / "climatology.zarr")
    os.environ["FRONTEND_DIR"] = str(tmp / "nonexistent")
    os.environ.setdefault("COPERNICUSMARINE_SERVICE_USERNAME", "ci")
    os.environ.setdefault("COPERNICUSMARINE_SERVICE_PASSWORD", "ci")

    # Inject the fake CMS SDK before any test imports app.sst.
    sys.modules["copernicusmarine"] = _make_fake_copernicusmarine()

    # Refresh the Settings singleton with the new env.
    from app.config import get_settings
    get_settings.cache_clear()

    # Materialise the substrate.
    _write_synthetic_substrate(tmp)


@pytest.fixture(autouse=True)
def _reset_request_state() -> None:
    """Drop in-memory caches between tests so they don't leak ETag / event state."""
    yield
    try:
        from app.routers.events import clear_event_cache, clear_response_cache
        clear_event_cache()
        clear_response_cache()
    except ImportError:
        pass
    try:
        from app.overlays import clear_overlay_cache
        clear_overlay_cache()
    except ImportError:
        pass
    try:
        from app.sst import _open_climatology_cached
        _open_climatology_cached.cache_clear()
    except ImportError:
        pass
    # Anomaly PNG LRU (added 2026-05-03 with the 4× upsample) — module-level
    # state that survives between tests and causes a 200 return where a 503
    # is expected if a prior test populated the cache for the same date+fmt.
    try:
        from app.routers.anomaly import clear_anomaly_cache
        clear_anomaly_cache()
    except ImportError:
        pass


@pytest.fixture()
def client():
    """FastAPI TestClient bound to the app."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


# Re-export helpers so tests can rebuild the substrate against a custom
# tmp_path or end date if they need to.
__all__ = [
    "_LATS",
    "_LONS",
    "_CUBE_DAYS",
    "_CLIM_START_YEAR",
    "_CLIM_END_YEAR",
    "_build_synthetic_sst_cube",
    "_build_synthetic_climatology",
    "_build_synthetic_subset_nc",
    "_write_synthetic_substrate",
    "_make_fake_copernicusmarine",
]
