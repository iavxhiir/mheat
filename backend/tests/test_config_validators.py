"""Tests for :class:`Settings` validators."""

from __future__ import annotations

import pytest

from app.config import Settings


def test_default_settings_parse_cleanly():
    s = Settings()
    assert s.clim_start < s.clim_end
    assert s.bbox_tuple == (-6.0, 30.0, 36.5, 46.0)


@pytest.mark.parametrize(
    "bad_bbox",
    [
        "only,three,values",
        "1,2,3,4,5",
        "not,numeric,values,nope",
        "200,0,201,1",          # lon out of range
        "0,-91,1,0",            # lat out of range
        "10,10,10,10",          # degenerate
        "20,10,10,20",          # min > max on lon axis
    ],
)
def test_invalid_bbox_is_rejected(bad_bbox):
    with pytest.raises(ValueError):
        Settings(bbox=bad_bbox)


def test_climatology_must_span_at_least_ten_years():
    with pytest.raises(ValueError, match="at least 10 years"):
        Settings(clim_start=2020, clim_end=2025)


def test_climatology_endpoints_must_be_ordered():
    with pytest.raises(ValueError, match="strictly less than"):
        Settings(clim_start=2020, clim_end=2020)
    with pytest.raises(ValueError, match="strictly less than"):
        Settings(clim_start=2025, clim_end=2020)


def test_valid_custom_settings_accepted():
    s = Settings(
        bbox="0,0,1,1",
        clim_start=1990,
        clim_end=2020,
    )
    assert s.bbox == "0,0,1,1"
    assert s.clim_end - s.clim_start == 30
