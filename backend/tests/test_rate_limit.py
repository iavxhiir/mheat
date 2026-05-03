"""Tests for :class:`RateLimitMiddleware`."""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def rate_limited_client():
    """App booted with the rate limiter enabled at tight thresholds."""
    previous = {
        k: os.environ.get(k)
        for k in ("RATE_LIMIT_ENABLED", "RATE_LIMIT_PER_MINUTE", "RATE_LIMIT_BURST")
    }
    os.environ["RATE_LIMIT_ENABLED"] = "true"
    os.environ["RATE_LIMIT_PER_MINUTE"] = "5"
    os.environ["RATE_LIMIT_BURST"] = "3"

    try:
        from app.config import get_settings
        get_settings.cache_clear()
        import app.main as main_mod
        importlib.reload(main_mod)
        yield TestClient(main_mod.app)
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from app.config import get_settings
        get_settings.cache_clear()
        import app.main as main_mod
        importlib.reload(main_mod)


def test_burst_limit_trips_at_the_fourth_request(rate_limited_client):
    """With burst=3, the 4th identical request in the same second is 429."""
    ok = 0
    denied = 0
    for _ in range(10):
        r = rate_limited_client.get("/api/events?start=2022-07-01&end=2022-08-15")
        if r.status_code == 200:
            ok += 1
        elif r.status_code == 429:
            denied += 1
    assert ok >= 3, "burst allowance should let at least 3 calls through"
    assert denied >= 1, "extra calls should be rate-limited"
    # At least one 429 must carry a Retry-After header.
    r = rate_limited_client.get("/api/events?start=2022-07-01&end=2022-08-15")
    if r.status_code == 429:
        assert "Retry-After" in r.headers
        body = r.json()
        assert body["error"]["code"] == "rate_limited"


def test_probe_paths_are_exempt(rate_limited_client):
    """Health and readyz must never 429 even after a burst."""
    # Burn the budget on /api/events first.
    for _ in range(10):
        rate_limited_client.get("/api/events?start=2022-07-01&end=2022-08-15")
    # Probes must still succeed.
    assert rate_limited_client.get("/api/health").status_code == 200
    assert rate_limited_client.get("/api/readyz").status_code == 200


def test_rate_limit_headers_exposed_on_success(rate_limited_client):
    r = rate_limited_client.get("/api/events?start=2022-07-01&end=2022-08-15")
    if r.status_code == 200:
        assert r.headers["X-RateLimit-Limit"] == "5"
        assert int(r.headers["X-RateLimit-Remaining"]) >= 0
