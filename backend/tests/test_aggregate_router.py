"""Tests for /api/aggregate."""

from __future__ import annotations

import pytest


def test_aggregate_by_year_returns_sorted_buckets(client):
    r = client.get("/api/aggregate?by=year&start=2022-05-15&end=2022-09-15")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["by"] == "year"
    assert isinstance(body["buckets"], list)
    if body["buckets"]:
        keys = [b["key"] for b in body["buckets"]]
        assert keys == sorted(keys), "year buckets must be ascending"
        for b in body["buckets"]:
            assert {"key", "count", "intensity_max", "intensity_mean",
                    "n_pixels_total", "aquaculture_sites", "mpa_area_km2",
                    "seagrass_area_km2"} <= set(b.keys())


def test_aggregate_by_category_returns_descending_count(client):
    r = client.get("/api/aggregate?by=category&start=2022-05-15&end=2022-09-15")
    assert r.status_code == 200
    body = r.json()
    counts = [b["count"] for b in body["buckets"]]
    assert counts == sorted(counts, reverse=True), "category buckets must be by descending count"
    if body["buckets"]:
        for b in body["buckets"]:
            assert "category_name" in b


def test_aggregate_by_country_with_med_events(client):
    r = client.get("/api/aggregate?by=country&start=2022-05-15&end=2022-09-15")
    assert r.status_code == 200
    body = r.json()
    assert body["by"] == "country"
    # Country keys should be ISO-2 (or "??" if outside Med)
    for b in body["buckets"]:
        assert len(b["key"]) == 2


def test_aggregate_by_mpa_two_buckets_max(client):
    r = client.get("/api/aggregate?by=mpa&start=2022-05-15&end=2022-09-15")
    assert r.status_code == 200
    body = r.json()
    keys = {b["key"] for b in body["buckets"]}
    assert keys <= {"events_touching_mpa", "events_not_touching_mpa"}


def test_aggregate_unknown_by_returns_422(client):
    r = client.get("/api/aggregate?by=banana")
    # 422 because Literal validation — not a runtime 400
    assert r.status_code in (400, 422)


def test_aggregate_no_dates_uses_default_window(client):
    """Should default to the events-router rolling 30-day window when no dates."""
    r = client.get("/api/aggregate?by=year")
    # 200 if cube is present; 503 climatology_missing is also acceptable in CI
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert body["start"] is not None
        assert body["end"] is not None
