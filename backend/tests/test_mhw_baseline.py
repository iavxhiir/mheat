"""Unit tests for the baseline-driven MHW detection path.

Two new code paths under test:

* ``detect_series_with_baseline`` — pure event-detection given pre-computed
  ``seas`` and ``thresh`` arrays (skips climatology rebuild on every call).
* ``detect_cube(..., baseline=...)`` — accepts a :class:`Climatology` and
  delegates to ``detect_series_with_baseline`` per pixel.

The fast path must agree with the legacy ``marineHeatWaves.detect`` path on
event counts and date alignment, modulo a 1-day edge wobble caused by the
percentile-smoothing kernel.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import xarray as xr

from app.climatology import Climatology, build_climatology_from_cube
from app.mhw import detect_cube, detect_series_with_baseline


# ---------------------------------------------------------------------
# detect_series_with_baseline
# ---------------------------------------------------------------------
def test_detect_series_with_baseline_finds_event() -> None:
    """A clearly super-threshold spike of >=5 days must be picked up as an event.

    Builds a 60-day series with mostly 20°C noise and an 8-day +5°C spike.
    seas is flat at 20, thresh flat at 23 — so the spike sits 2°C above
    threshold and 5°C above seas (intensity_max ≈ 5).
    """
    times = pd.date_range("2022-06-01", periods=60, freq="D").values
    rng = np.random.default_rng(0)
    sst = 20.0 + rng.normal(scale=0.2, size=60)
    sst[20:28] = 25.0  # 8-day super-threshold spike
    seas = np.full(60, 20.0)
    thresh = np.full(60, 23.0)

    res = detect_series_with_baseline(times, sst, seas, thresh)
    mhws = res["mhws"]
    assert mhws["n_events"] >= 1
    # Locate the event covering our spike.
    hit = None
    for i in range(mhws["n_events"]):
        if mhws["index_start"][i] <= 27 and mhws["index_end"][i] >= 20:
            hit = i
            break
    assert hit is not None, "injected spike was not detected"
    assert mhws["duration"][hit] >= 5
    assert abs(mhws["intensity_max"][hit] - 5.0) < 0.5


def test_detect_series_with_baseline_respects_min_duration() -> None:
    """A 3-day spike must be discarded — Hobday minDuration=5."""
    times = pd.date_range("2022-06-01", periods=60, freq="D").values
    sst = np.full(60, 20.0)
    sst[20:23] = 25.0  # 3-day spike — too short
    seas = np.full(60, 20.0)
    thresh = np.full(60, 23.0)

    res = detect_series_with_baseline(times, sst, seas, thresh)
    assert res["mhws"]["n_events"] == 0


def test_detect_series_with_baseline_joins_small_gaps() -> None:
    """Two qualifying events separated by ≤2 days must merge into one."""
    times = pd.date_range("2022-06-01", periods=60, freq="D").values
    sst = np.full(60, 20.0)
    sst[10:15] = 25.0  # spike A: 5 days
    # gap of 2 days at indices 15..16
    sst[17:22] = 25.0  # spike B: 5 days
    seas = np.full(60, 20.0)
    thresh = np.full(60, 23.0)

    res = detect_series_with_baseline(times, sst, seas, thresh)
    mhws = res["mhws"]
    assert mhws["n_events"] == 1, f"expected merged event, got {mhws['n_events']}"
    # Merged duration spans index 10..21 inclusive = 12 days.
    assert mhws["duration"][0] == 12
    assert mhws["index_start"][0] == 10
    assert mhws["index_end"][0] == 21


def test_detect_series_with_baseline_nan_safe() -> None:
    """NaN SST values must not crash the detector or count as exceedances."""
    times = pd.date_range("2022-06-01", periods=60, freq="D").values
    sst = np.full(60, 20.0)
    sst[20:28] = 25.0  # 8-day spike
    sst[22] = np.nan  # poke a hole in the middle
    sst[24] = np.nan
    seas = np.full(60, 20.0)
    thresh = np.full(60, 23.0)

    # Must not raise.
    res = detect_series_with_baseline(times, sst, seas, thresh)
    mhws = res["mhws"]
    # Still detects the spike (NaN days don't break the contiguous run because
    # ``exceed`` is False there — but they also don't extend it either).
    # The exact count depends on the ndimage label split caused by the holes;
    # we only assert no crash and that *something* sensible is reported.
    assert isinstance(mhws["n_events"], int)
    # NaN days must NOT be flagged as exceedances.
    assert bool(res["clim"]["missing"][22])
    assert bool(res["clim"]["missing"][24])


# ---------------------------------------------------------------------
# detect_cube + baseline parity
# ---------------------------------------------------------------------
def _parity_cube() -> xr.DataArray:
    """5-year, single-pixel-effective synthetic cube with one obvious event.

    A 1×1 grid would force coarsening corner-cases; instead use 2×2 with
    identical values so per-pixel detection is deterministic.
    """
    times = pd.date_range("1990-01-01", periods=365 * 5, freq="D")
    doy = np.array([t.dayofyear for t in times])
    base = 18.0 + 5.0 * np.sin((doy - 80) * 2 * np.pi / 365.25)
    rng = np.random.default_rng(7)
    sst = base + rng.normal(scale=0.2, size=len(times))
    # Inject a 12-day +4°C anomaly in year 4 (well inside clim window).
    sst[365 * 3 + 200 : 365 * 3 + 212] += 4.0
    cube = np.broadcast_to(sst[:, None, None], (len(times), 2, 2)).astype("float32").copy()
    return xr.DataArray(
        cube,
        dims=("time", "latitude", "longitude"),
        coords={
            "time": times,
            "latitude": np.array([40.0, 40.25], dtype="float32"),
            "longitude": np.array([10.0, 10.25], dtype="float32"),
        },
    )


def test_detect_cube_with_baseline_vs_legacy_parity() -> None:
    """Legacy and baseline-driven detection must agree on event counts and dates.

    Builds the baseline from the same cube via ``build_climatology_from_cube``
    so both paths see identical reference statistics. Allow ±1 day on edges
    because the 31-day percentile smoother and the upstream library handle
    DOY wrap-around very slightly differently at the year boundary.
    """
    cube = _parity_cube()

    legacy = detect_cube(cube, clim_period=(1990, 1994), max_pixels=4)
    baseline = build_climatology_from_cube(cube, clim_start=1990, clim_end=1994)
    fast = detect_cube(cube, baseline=baseline, max_pixels=4)

    assert len(legacy) == len(fast), (
        f"event count mismatch: legacy={len(legacy)} fast={len(fast)}"
    )

    # Compare per-pixel events sorted by start date.
    def _sorted(events):
        return sorted(events, key=lambda e: (e.centroid_lat, e.centroid_lon, e.date_start))

    for a, b in zip(_sorted(legacy), _sorted(fast)):
        ds_a = pd.Timestamp(a.date_start)
        ds_b = pd.Timestamp(b.date_start)
        de_a = pd.Timestamp(a.date_end)
        de_b = pd.Timestamp(b.date_end)
        assert abs((ds_a - ds_b).days) <= 1, f"start mismatch: {a.date_start} vs {b.date_start}"
        assert abs((de_a - de_b).days) <= 1, f"end mismatch: {a.date_end} vs {b.date_end}"


def test_detect_cube_baseline_fallback_on_empty_baseline(caplog) -> None:
    """A bbox-mismatched baseline must trigger the warning + legacy fallback path.

    The cube is in (lat=40, lon=10) and the baseline is built far away
    (lat=0, lon=0). When ``_align_baseline_to_grid`` snaps to nearest
    neighbours that may still find points, but selecting outside the bbox
    via slice_bbox returns an empty grid which then fails alignment → fallback.
    """
    cube = _parity_cube()

    # Build a tiny baseline with *no* spatial overlap at all — empty after
    # slicing to the cube bbox. Easiest way: build the climatology then slice
    # it to a non-overlapping window so its grid is empty.
    full = build_climatology_from_cube(cube, clim_start=1990, clim_end=1994)
    empty = full.slice_bbox((100.0, -10.0, 110.0, 0.0))  # disjoint from cube

    with caplog.at_level(logging.WARNING, logger="app.mhw"):
        events = detect_cube(cube, baseline=empty, max_pixels=4)

    # Must not crash; fallback may legitimately yield 0 or more events.
    assert isinstance(events, list)
    # A warning was emitted (we don't assert exact text — just that the
    # alignment path complained about something).
    assert any("Baseline" in rec.message or "fall" in rec.message.lower()
               for rec in caplog.records), "expected fallback warning"
