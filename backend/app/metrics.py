"""Prometheus metrics for MHEAT.

Opt-in via ``METRICS_ENABLED=true`` (default off). When enabled:

* Installs a Starlette middleware that records an HTTP request counter and
  a latency histogram, labelled by method, route template and status code.
* Exposes a ``/api/metrics`` endpoint rendering the default registry in
  Prometheus text exposition format.

Scientific pipeline stages also get dedicated histograms / counters so the
same signals that appear in OpenTelemetry spans are scrape-able without an
OTLP collector:

* ``mheat_mhw_detect_duration_seconds``  — histogram, per-pixel detection.
* ``mheat_mhw_cluster_duration_seconds`` — histogram, space-time clustering.
* ``mheat_mhw_impact_duration_seconds``  — histogram, events × overlays join.
* ``mheat_mhw_events_detected_total``     — counter, event clusters produced.

Both the middleware and the /metrics route are no-ops when
``METRICS_ENABLED`` is off or when ``prometheus_client`` is not installed,
so cold-start cost is zero in minimal builds.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_ENABLED = False
_REQUESTS: Any = None
_LATENCY: Any = None
_DETECT: Any = None
_CLUSTER: Any = None
_IMPACT: Any = None
_EVENTS: Any = None
_CLIM_CACHE_HITS: Any = None
_CLIM_CACHE_MISSES: Any = None
_BASELINE_USED: Any = None
_CLIM_AGE_DAYS: Any = None
_CONTENT_TYPE: str = "text/plain; version=0.0.4; charset=utf-8"
_generate_latest: Callable[..., bytes] | None = None


def _try_import_prometheus() -> tuple[str, Any, Any, Any, Any] | None:
    try:
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            Counter,
            Gauge,
            Histogram,
            generate_latest,
        )
    except Exception:  # noqa: BLE001
        return None
    return CONTENT_TYPE_LATEST, Counter, Histogram, Gauge, generate_latest


def is_enabled() -> bool:
    """Whether metrics have been initialised and are being recorded."""
    return _ENABLED


def init_metrics() -> bool:
    """Install metric collectors. Returns True when metrics are active.

    Safe to call multiple times; only the first call wires collectors.
    """
    global _ENABLED, _REQUESTS, _LATENCY, _DETECT, _CLUSTER, _IMPACT, _EVENTS
    global _CLIM_CACHE_HITS, _CLIM_CACHE_MISSES, _BASELINE_USED, _CLIM_AGE_DAYS
    global _CONTENT_TYPE, _generate_latest

    if _ENABLED:
        return True

    if os.environ.get("METRICS_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return False

    bits = _try_import_prometheus()
    if bits is None:
        logger.warning("METRICS_ENABLED set but prometheus_client not installed — metrics disabled")
        return False
    CONTENT_TYPE_LATEST, Counter, Histogram, Gauge, generate_latest = bits

    _CONTENT_TYPE = CONTENT_TYPE_LATEST
    _generate_latest = generate_latest

    _REQUESTS = Counter(
        "mheat_http_requests_total",
        "HTTP requests, labelled by method, route template and status.",
        ["method", "route", "status"],
    )
    _LATENCY = Histogram(
        "mheat_http_request_duration_seconds",
        "HTTP request latency in seconds.",
        ["method", "route"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _DETECT = Histogram(
        "mheat_mhw_detect_duration_seconds",
        "Wall-clock duration of the per-pixel Hobday detector.",
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )
    _CLUSTER = Histogram(
        "mheat_mhw_cluster_duration_seconds",
        "Wall-clock duration of the space-time clustering step.",
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _IMPACT = Histogram(
        "mheat_mhw_impact_duration_seconds",
        "Wall-clock duration of the events × overlays impact join.",
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    _EVENTS = Counter(
        "mheat_mhw_events_detected_total",
        "Total event clusters produced by the detect pipeline.",
    )
    _CLIM_CACHE_HITS = Counter(
        "mheat_climatology_cache_hits_total",
        "Climatology zarr opens served from the in-process LRU cache.",
    )
    _CLIM_CACHE_MISSES = Counter(
        "mheat_climatology_cache_misses_total",
        "Climatology zarr opens that had to read from disk (cache miss).",
    )
    _BASELINE_USED = Counter(
        "mheat_detection_baseline_used_total",
        "Detection invocations split by whether a precomputed baseline was supplied.",
        ["mode"],
    )
    _CLIM_AGE_DAYS = Gauge(
        "mheat_climatology_artifact_age_days",
        "Age in days of the climatology artifact (now - attrs.created_utc).",
    )

    _ENABLED = True
    logger.info("Prometheus metrics enabled on /api/metrics")
    return True


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP counter + latency histogram for every request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not _ENABLED:
            return await call_next(request)
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = _route_template(request)
            dur = time.perf_counter() - t0
            _REQUESTS.labels(request.method, route, str(status)).inc()
            _LATENCY.labels(request.method, route).observe(dur)


def _route_template(request: Request) -> str:
    """Return the route template (e.g. ``/api/events/{event_id}/series``).

    Falls back to the raw path if no route has been matched yet (this
    happens on 404s where Starlette never resolved an endpoint).
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or request.url.path


@contextlib.contextmanager
def observe_stage(name: str) -> Iterator[None]:
    """Record a scientific-pipeline stage duration if enabled.

    Accepts ``name`` in {``detect``, ``cluster``, ``impact``}. Unknown
    names silently fall through so call sites can add stages without
    coordination.
    """
    if not _ENABLED:
        yield
        return
    mapping = {"detect": _DETECT, "cluster": _CLUSTER, "impact": _IMPACT}
    hist = mapping.get(name)
    if hist is None:
        yield
        return
    with hist.time():
        yield


def inc_events_detected(n: int) -> None:
    """Add ``n`` to the ``mheat_mhw_events_detected_total`` counter."""
    if not _ENABLED or n <= 0:
        return
    _EVENTS.inc(n)


def inc_climatology_cache_hit() -> None:
    """Record one climatology cache hit (LRU returned a previously opened object)."""
    if not _ENABLED:
        return
    _CLIM_CACHE_HITS.inc()


def inc_climatology_cache_miss() -> None:
    """Record one climatology cache miss (zarr was opened from disk)."""
    if not _ENABLED:
        return
    _CLIM_CACHE_MISSES.inc()


def inc_baseline_used(mode: str) -> None:
    """Record one detect_cube invocation with ``mode`` ∈ {``baseline``, ``legacy``}."""
    if not _ENABLED:
        return
    _BASELINE_USED.labels(mode).inc()


def set_climatology_age_days(value: float) -> None:
    """Set the climatology artifact age (days). NaN ⇒ unknown / absent."""
    if not _ENABLED:
        return
    _CLIM_AGE_DAYS.set(value)


def render_latest() -> tuple[bytes, str]:
    """Serialise the default registry as Prometheus exposition text."""
    if not _ENABLED or _generate_latest is None:
        return b"", _CONTENT_TYPE
    return _generate_latest(), _CONTENT_TYPE
