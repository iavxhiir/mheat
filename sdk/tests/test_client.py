"""Live-backend tests for ``mheat_client.MheatClient``.

These tests hit the running MHEAT backend at ``MHEAT_TEST_URL`` (default
``http://127.0.0.1:8000``). Each test is skipped automatically if the backend
is unreachable, so the suite is safe to run on a developer laptop without the
service started, on CI that doesn't bring up the API, or on a Datalab kernel.

Run::

    cd sdk
    pip install -e '.[dev,geo,plot]'
    pytest -v tests/

To point at a deployed backend::

    MHEAT_TEST_URL=https://mhw.edito.example.com pytest -v tests/
"""

from __future__ import annotations

import os
from typing import Iterator

import httpx
import pandas as pd
import pytest
import xarray as xr

from mheat_client import MheatClient

BASE_URL = os.environ.get("MHEAT_TEST_URL", "http://127.0.0.1:8000")


def _backend_alive() -> bool:
    """Cheap reachability check with up to 3 retries.

    /api/health is constant-time on the server, but the request loop can be
    busy during a CMS pull, causing transient 5-15s blips. We retry a few
    times so the suite doesn't skip on a momentarily-busy backend, and skip
    cleanly when the backend is genuinely down (e.g. CI without the API).
    """
    import time
    for attempt in range(3):
        try:
            with httpx.Client(timeout=10.0) as c:
                r = c.get(f"{BASE_URL}/api/health")
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


pytestmark = pytest.mark.skipif(
    not _backend_alive(),
    reason=f"MHEAT backend at {BASE_URL} is not reachable — skipping live tests.",
)


@pytest.fixture(scope="module")
def client() -> Iterator[MheatClient]:
    # 180s tolerates a backend mid-CMS-pull. The /api/events.parquet endpoint
    # can take 20-60s on the first hit if the cube is being refreshed.
    with MheatClient(BASE_URL, timeout=180.0) as c:
        yield c


def test_health_returns_ok(client: MheatClient) -> None:
    h = client.health()
    assert isinstance(h, dict)
    assert h.get("status") == "ok"
    assert "version" in h and isinstance(h["version"], str)


def test_freshness_and_extent_shape(client: MheatClient) -> None:
    fresh = client.freshness()
    assert isinstance(fresh, dict)
    # Either keys exist, depending on whether a pull has happened yet.
    assert "cube_start" in fresh and "cube_end" in fresh

    ext = client.extent()
    assert isinstance(ext, dict)
    assert "start" in ext and "end" in ext
    assert "n_days" in ext and ext["n_days"] > 0


def test_events_returns_dataframe(client: MheatClient) -> None:
    df = client.events(start="2022-05-15", end="2022-09-15", min_category=1)
    assert isinstance(df, pd.DataFrame)
    # Backend ships the Med-2022 events; fixture data shows ~36 events at min_cat=1.
    assert len(df) > 0
    expected = {
        "event_id",
        "date_start",
        "date_end",
        "date_peak",
        "intensity_max",
        "category",
        "category_name",
        "centroid_lon",
        "centroid_lat",
        "geometry",
    }
    assert expected.issubset(df.columns), f"missing: {expected - set(df.columns)}"
    # date_start was coerced to datetime by the SDK.
    assert pd.api.types.is_datetime64_any_dtype(df["date_start"])
    # min_category filter is honoured.
    assert int(df["category"].min()) >= 1


def test_event_series_returns_indexed_df(client: MheatClient) -> None:
    # Pick the largest event in our window so any pixel near its centroid hits.
    df = client.events(start="2022-05-15", end="2022-09-15", min_category=3)
    assert len(df) > 0, "no Cat-III+ events in 2022-summer test window"
    hottest = df.sort_values("intensity_max", ascending=False).iloc[0]
    series = client.event_series(
        event_id=str(hottest["event_id"]),
        lon=float(hottest["centroid_lon"]),
        lat=float(hottest["centroid_lat"]),
        pad_days=10,
    )
    assert isinstance(series, pd.DataFrame)
    assert {"sst", "seas", "thresh"}.issubset(series.columns)
    assert isinstance(series.index, pd.DatetimeIndex)
    assert len(series) > 0
    assert series.attrs.get("event_id") == hottest["event_id"]


def test_sst_cube_opens_lazily(client: MheatClient) -> None:
    # This is heavier (touches the consolidated Zarr metadata) so we wrap in a
    # try/skip — some CI environments block outbound HTTP from xarray.
    try:
        ds = client.sst_cube()
    except Exception as e:
        pytest.skip(f"Zarr cube not openable in this environment: {e}")
    assert isinstance(ds, xr.Dataset)
    # Check we got something with a time dimension.
    assert "time" in ds.dims or "time" in ds.coords
    # And at least one variable that smells like SST.
    var_names = list(ds.data_vars)
    assert any("sst" in v.lower() or "temp" in v.lower() for v in var_names), (
        f"no SST-like variable in cube: {var_names}"
    )
