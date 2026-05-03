"""Tests for the Prometheus metrics integration (`app.metrics`, `/api/metrics`)."""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def metrics_client(monkeypatch):
    """Spin up a fresh app with METRICS_ENABLED=true so the endpoint is live.

    The metrics module caches state via module-level globals (counters and
    histograms must not be re-registered on the default prometheus registry),
    so the fixture reloads the relevant modules inside a clean registry.
    """
    monkeypatch.setenv("METRICS_ENABLED", "true")
    # Wipe prometheus_client's default registry so repeated test runs don't
    # collide on "Duplicated timeseries in CollectorRegistry".
    from prometheus_client import REGISTRY
    # Collect the collectors directly (list() snapshots it) and unregister each.
    for c in list(REGISTRY._collector_to_names):  # type: ignore[attr-defined]
        try:
            REGISTRY.unregister(c)
        except KeyError:
            pass

    # Force re-init of our metrics module state.
    from app import metrics as m
    m._ENABLED = False  # type: ignore[attr-defined]
    m._REQUESTS = None  # type: ignore[attr-defined]
    m._LATENCY = None  # type: ignore[attr-defined]
    m._DETECT = None  # type: ignore[attr-defined]
    m._CLUSTER = None  # type: ignore[attr-defined]
    m._IMPACT = None  # type: ignore[attr-defined]
    m._EVENTS = None  # type: ignore[attr-defined]

    # Re-import main so create_app() runs with METRICS_ENABLED on.
    import app.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_metrics_endpoint_returns_404_when_disabled(client):
    r = client.get("/api/metrics")
    assert r.status_code == 404
    body = r.json()
    # Envelope wraps the detail; both code and message are stable.
    assert body["error"]["code"] == "metrics_disabled"
    assert body["error"]["message"] == "metrics disabled"


def test_metrics_endpoint_exposes_prometheus_text_when_enabled(metrics_client):
    r = metrics_client.get("/api/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # Prometheus exposition format always ships HELP/TYPE lines for registered metrics.
    assert "# HELP mheat_http_requests_total" in body
    assert "# TYPE mheat_http_requests_total counter" in body
    assert "# HELP mheat_http_request_duration_seconds" in body
    assert "# TYPE mheat_http_request_duration_seconds histogram" in body


def test_http_request_counter_increments_on_request(metrics_client):
    # Fire a healthy request to drive the middleware.
    r = metrics_client.get("/api/health")
    assert r.status_code == 200
    body = metrics_client.get("/api/metrics").text
    # Counter line with method+route+status labels must show at least 1 hit.
    assert 'mheat_http_requests_total{method="GET",route="/api/health",status="200"}' in body


def test_scientific_stage_histograms_are_registered(metrics_client):
    body = metrics_client.get("/api/metrics").text
    for stage in ("detect", "cluster", "impact"):
        assert f"mheat_mhw_{stage}_duration_seconds" in body
    assert "mheat_mhw_events_detected_total" in body


def test_observe_stage_is_noop_when_disabled():
    """With METRICS_ENABLED unset the context manager must not raise."""
    from app.metrics import observe_stage, inc_events_detected

    with observe_stage("detect"):
        pass
    with observe_stage("unknown-stage"):
        pass
    inc_events_detected(0)  # no-op
    inc_events_detected(5)  # no-op (disabled)
