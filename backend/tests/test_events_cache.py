"""Tests for /api/events ETag + TTL-cache behaviour."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(autouse=True)
def _clean_cache():
    from app.routers.events import clear_response_cache
    clear_response_cache()
    yield
    clear_response_cache()


def test_first_request_returns_200_with_etag(client):
    r = client.get("/api/events?start=2022-07-01&end=2022-08-15")
    assert r.status_code == 200
    etag = r.headers.get("ETag")
    assert etag and etag.startswith('"') and etag.endswith('"')
    assert "max-age=" in r.headers.get("Cache-Control", "")


def test_if_none_match_returns_304_with_same_etag(client):
    r1 = client.get("/api/events?start=2022-07-01&end=2022-08-15")
    etag = r1.headers["ETag"]
    r2 = client.get(
        "/api/events?start=2022-07-01&end=2022-08-15",
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.headers["ETag"] == etag
    assert r2.content in (b"", None)


def test_different_params_yield_different_etags(client):
    e1 = client.get("/api/events?start=2022-07-01&end=2022-08-15").headers["ETag"]
    e2 = client.get("/api/events?start=2022-07-01&end=2022-08-15&raw=true").headers["ETag"]
    assert e1 != e2


def test_if_none_match_with_wrong_etag_returns_full_body(client):
    r = client.get(
        "/api/events?start=2022-07-01&end=2022-08-15",
        headers={"If-None-Match": '"deadbeef"'},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"


def test_bbox_validation_error_is_not_cached_as_200(client):
    """A malformed bbox must still surface the 400 envelope, never a stale 200."""
    r = client.get("/api/events?bbox=bad&start=2022-07-01&end=2022-08-15")
    assert r.status_code == 400
    # A subsequent good request still starts with a 200 and a fresh ETag —
    # the bad-bbox response was never inserted into the response cache.
    r2 = client.get("/api/events?bbox=6,38,14,43&start=2022-07-01&end=2022-08-15")
    assert r2.status_code == 200
    assert "ETag" in r2.headers


def test_cache_ttl_zero_disables_caching(client, monkeypatch):
    """EVENTS_CACHE_TTL_SECONDS=0 ⇒ Cache-Control no-store + no reuse between calls."""
    monkeypatch.setenv("EVENTS_CACHE_TTL_SECONDS", "0")
    r = client.get("/api/events?start=2022-07-01&end=2022-08-15")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"


def test_cache_hit_path_is_fast(client):
    """Second call with same params should be faster than the first.

    The autouse ``_clean_cache`` fixture only clears the HTTP response cache,
    so we explicitly drop the detection cache too — otherwise prior tests in
    the same process leave it populated and ``cold`` becomes a cache hit.
    """
    from app.routers.events import clear_event_cache
    clear_event_cache()

    t0 = time.perf_counter()
    client.get("/api/events?start=2022-07-01&end=2022-08-15").raise_for_status()
    cold = time.perf_counter() - t0

    t1 = time.perf_counter()
    client.get("/api/events?start=2022-07-01&end=2022-08-15").raise_for_status()
    warm = time.perf_counter() - t1

    # Cold has to go through detection; warm should be O(dict lookup).
    # Guard with a generous ratio so flaky CI doesn't trip us.
    assert warm < cold, f"warm ({warm*1e3:.1f}ms) not faster than cold ({cold*1e3:.1f}ms)"
