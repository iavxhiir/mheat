"""Unit tests for Hobday MHW detection, clustering, filtering, and categories."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app.mhw import (
    CATEGORY_NAMES,
    MhwEvent,
    _category_index,
    cluster_events,
    detect_cube,
    detect_series,
    filter_events,
)


# ---------------------------------------------------------------------
def _synthetic_series(n_years: int = 4):
    times = pd.date_range("2018-01-01", periods=365 * n_years, freq="D")
    doy = np.array([t.dayofyear for t in times])
    rng = np.random.default_rng(0)
    sst = 18.0 + 5.0 * np.sin((doy - 80) * 2 * np.pi / 365.25) + rng.normal(scale=0.3, size=len(times))
    event_slice = slice(365 * 2 + 180, 365 * 2 + 195)
    sst[event_slice] += 4.0
    return times, sst, event_slice


def test_detect_series_finds_injected_event() -> None:
    times, sst, event_slice = _synthetic_series()
    result = detect_series(times.values, sst, clim_period=(2018, 2021))
    mhws = result["mhws"]
    assert mhws["n_events"] >= 1

    injected_start = times[event_slice.start].to_pydatetime().toordinal()
    injected_end = times[event_slice.stop - 1].to_pydatetime().toordinal()

    hit = False
    for i in range(mhws["n_events"]):
        s = mhws["time_start"][i]
        e = mhws["time_end"][i]
        if s <= injected_end and e >= injected_start:
            hit = True
            assert mhws["duration"][i] >= 5
            assert mhws["intensity_max"][i] > 1.5
    assert hit


def test_detect_cube_returns_events() -> None:
    times, sst, _ = _synthetic_series()
    lats = np.array([40.0, 40.25, 40.5], dtype="float32")
    lons = np.array([10.0, 10.25, 10.5], dtype="float32")
    cube = np.broadcast_to(sst[:, None, None], (len(times), 3, 3)).copy()
    for iy in range(3):
        for ix in range(3):
            if not (iy == 1 and ix == 1):
                cube[:, iy, ix] -= 4.0 * (
                    (np.arange(len(times)) >= 365 * 2 + 180)
                    & (np.arange(len(times)) < 365 * 2 + 195)
                )
    da = xr.DataArray(
        cube.astype("float32"),
        dims=("time", "latitude", "longitude"),
        coords={"time": times, "latitude": lats, "longitude": lons},
    )
    events = detect_cube(da, clim_period=(2018, 2021), max_pixels=9)
    assert len(events) >= 1
    assert any(e.category >= 1 for e in events)
    assert any(abs(e.centroid_lat - 40.25) < 1e-3 and abs(e.centroid_lon - 10.25) < 1e-3 for e in events)


def test_detect_cube_no_events_edge_case() -> None:
    """A noisy but anomaly-free cube produces at most tiny spurious events
    (never a Category ≥ III). Guards against false-positive MHW storms in
    calm baseline data.
    """
    times = pd.date_range("2018-01-01", periods=365 * 3, freq="D")
    doy = np.array([t.dayofyear for t in times])
    rng = np.random.default_rng(1)
    base = 18.0 + 5.0 * np.sin((doy - 80) * 2 * np.pi / 365.25)
    cube = np.empty((len(times), 2, 2), dtype="float32")
    for iy in range(2):
        for ix in range(2):
            cube[:, iy, ix] = base + rng.normal(scale=0.3, size=len(times))
    da = xr.DataArray(
        cube,
        dims=("time", "latitude", "longitude"),
        coords={"time": times, "latitude": [40.0, 40.25], "longitude": [10.0, 10.25]},
    )
    events = detect_cube(da, clim_period=(2018, 2020), max_pixels=4)
    # Any events produced must be weak (I/II at worst).
    assert all(e.category <= 2 for e in events)


# ---------------------------------------------------------------------
def _mk_event(eid: str, d0: str, d1: str, lon: float, lat: float,
              cat: int = 2, intensity: float = 1.5) -> MhwEvent:
    return MhwEvent(
        event_id=eid,
        date_start=d0,
        date_end=d1,
        date_peak=d0,
        duration_days=(date.fromisoformat(d1) - date.fromisoformat(d0)).days + 1,
        intensity_max=intensity,
        intensity_mean=intensity * 0.6,
        intensity_cumulative=intensity * 5,
        category=cat,
        category_name=CATEGORY_NAMES[cat - 1],
        centroid_lon=lon,
        centroid_lat=lat,
        bbox=[lon - 0.125, lat - 0.125, lon + 0.125, lat + 0.125],
    )


def test_cluster_events_merges_nearby_overlapping() -> None:
    # Two nearby events overlapping in time → one cluster.
    e1 = _mk_event("a", "2022-07-01", "2022-07-10", 10.0, 41.0)
    e2 = _mk_event("b", "2022-07-05", "2022-07-15", 10.2, 41.1)
    clusters = cluster_events([e1, e2])
    assert len(clusters) == 1
    c = clusters[0]
    assert c.n_pixels == 2
    assert c.date_start == "2022-07-01"
    assert c.date_end == "2022-07-15"


def test_cluster_events_keeps_far_or_disjoint_separate() -> None:
    # Far-apart events → two clusters.
    e1 = _mk_event("a", "2022-07-01", "2022-07-10", 10.0, 41.0)
    e2 = _mk_event("b", "2022-07-05", "2022-07-15", 30.0, 41.0)
    # Temporally disjoint event at same location → third cluster.
    e3 = _mk_event("c", "2022-09-01", "2022-09-10", 10.05, 41.05)
    clusters = cluster_events([e1, e2, e3])
    assert len(clusters) == 3


def test_cluster_events_empty_input() -> None:
    assert cluster_events([]) == []


def test_cluster_reduces_med_demo_to_few() -> None:
    """Simulate 9 nearby pixel events → should collapse to 1 cluster."""
    events = [
        _mk_event(f"p{i}", "2022-07-10", "2022-07-20", 10.0 + 0.25 * (i % 3), 41.0 + 0.25 * (i // 3))
        for i in range(9)
    ]
    clusters = cluster_events(events)
    assert len(clusters) == 1
    assert clusters[0].n_pixels == 9


# ---------------------------------------------------------------------
def test_filter_events_date() -> None:
    e1 = _mk_event("a", "2022-07-01", "2022-07-10", 10.0, 41.0)
    e2 = _mk_event("b", "2022-08-01", "2022-08-10", 10.0, 41.0)
    out = filter_events([e1, e2], start=date(2022, 7, 15), end=date(2022, 8, 30))
    assert [e.event_id for e in out] == ["b"]


def test_filter_events_bbox() -> None:
    e1 = _mk_event("a", "2022-07-01", "2022-07-10", 5.0, 41.0)
    e2 = _mk_event("b", "2022-07-01", "2022-07-10", 15.0, 41.0)
    out = filter_events([e1, e2], bbox=(10.0, 40.0, 20.0, 42.0))
    assert [e.event_id for e in out] == ["b"]


# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "sst_max,clim,thresh,expected",
    [
        (22.0, 20.0, 21.0, 2),  # 2x ΔT → II Strong
        (20.5, 20.0, 21.0, 1),  # below threshold → I Moderate (floor)
        (25.0, 20.0, 21.0, 5),  # 5x → V clipped
        (24.0, 20.0, 21.0, 4),  # 4x → IV
        (23.0, 20.0, 21.0, 3),  # 3x → III
    ],
)
def test_category_index_range(sst_max, clim, thresh, expected) -> None:
    assert _category_index(sst_max, clim, thresh) == expected


@pytest.mark.parametrize("start,end", [("2020-06-01", "2020-08-31")])
def test_events_endpoint_runs_in_demo_mode(client, start: str, end: str) -> None:
    r = client.get(f"/api/events?start={start}&end={end}")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)
