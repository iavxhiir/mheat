"""HTTP middleware for MHEAT — request-id, structured logging, timing,
security headers.

Exposed middlewares:

* :class:`RequestIdMiddleware`       — assigns/propagates ``X-Request-Id``.
* :class:`AccessLogMiddleware`       — emits a structured access-log record
  per request.
* :class:`SecurityHeadersMiddleware` — sets CSP, X-Content-Type-Options,
  X-Frame-Options, Referrer-Policy, Permissions-Policy. Policy-string
  defaults are configurable via environment (``CSP_POLICY``,
  ``PERMISSIONS_POLICY``) so operators can relax them for third-party
  embeds without touching the image.
* :func:`timed_span`                  — context manager that records
  wall-clock duration under a configurable name.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Stash the current request id where log formatters can find it.
_REQUEST_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

logger = logging.getLogger("mheat.access")


def current_request_id() -> str:
    """Return the active request id (or ``-`` if not inside a request)."""
    return _REQUEST_ID_CTX.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign or propagate a UUID per HTTP request."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = _REQUEST_ID_CTX.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
        finally:
            _REQUEST_ID_CTX.reset(token)
        response.headers["X-Request-Id"] = rid
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit a structured access log per request."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        t0 = time.perf_counter()
        response: Response
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            dur_ms = (time.perf_counter() - t0) * 1000.0
            # Skip the per-tile PNG flood on the map
            path = request.url.path
            logger.info(
                "http_request",
                extra={
                    "path": path,
                    "method": request.method,
                    "status_code": status,
                    "duration_ms": round(dur_ms, 2),
                    "request_id": getattr(request.state, "request_id", "-"),
                },
            )


_RATE_LIMIT_EXCLUDED_PATHS: tuple[str, ...] = (
    "/api/health", "/api/readyz", "/api/metrics",
)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversize request bodies before they reach a handler.

    Default ceiling is **2 MiB** — MHEAT's largest legitimate POST body
    is the ``/api/processes/mhw-detect`` payload (~100 bytes). 2 MiB is
    a ~20 000× buffer, plenty of headroom while still shutting down a
    drive-by "POST me a gigabyte" at the door. Configurable via
    ``MAX_REQUEST_BODY_BYTES``; set to ``0`` to disable.
    """

    def __init__(self, app: Any, *, max_bytes: int | None = None) -> None:
        super().__init__(app)
        self._max_bytes = (
            max_bytes
            if max_bytes is not None
            else int(os.environ.get("MAX_REQUEST_BODY_BYTES", str(2 * 1024 * 1024)))
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._max_bytes <= 0:
            return await call_next(request)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    return _payload_too_large(request, self._max_bytes)
            except ValueError:
                return _payload_too_large(request, self._max_bytes)
        return await call_next(request)


def _payload_too_large(request: Request, max_bytes: int) -> Response:
    import json as _json

    body = {
        "error": {
            "code": "payload_too_large",
            "message": f"Request body exceeds the {max_bytes}-byte ceiling.",
            "status": 413,
            "request_id": getattr(request.state, "request_id", "-"),
        }
    }
    return Response(
        status_code=413,
        content=_json.dumps(body),
        media_type="application/json",
        headers={"Connection": "close"},
    )



class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP.

    Off by default — gate via ``RATE_LIMIT_ENABLED=true``. Thresholds:

    * ``RATE_LIMIT_PER_MINUTE`` (default 120) — steady-state cap.
    * ``RATE_LIMIT_BURST`` (default 20) — short-window allowance.

    Probe paths (``/api/health``, ``/api/readyz``, ``/api/metrics``) are
    always exempt so cluster autoscalers and Prometheus don't trip the
    limiter.

    Stateless across workers: the counter lives in-process. For multi-replica
    deployments, place a real rate limiter (ingress / Envoy) in front.
    """

    def __init__(
        self,
        app: Any,
        *,
        per_minute: int | None = None,
        burst: int | None = None,
    ) -> None:
        super().__init__(app)
        self._per_minute = per_minute or int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120"))
        self._burst = burst or int(os.environ.get("RATE_LIMIT_BURST", "20"))
        self._window_seconds = 60.0
        self._burst_window_seconds = 1.0
        # {ip: list[monotonic timestamps]}, pruned on access.
        self._hits: dict[str, list[float]] = {}

    @staticmethod
    def _client_ip(request: Request) -> str:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",", 1)[0].strip()
        return request.client.host if request.client else "-"

    def _prune_and_count(self, ip: str, now: float) -> tuple[int, int]:
        window_start = now - self._window_seconds
        burst_start = now - self._burst_window_seconds
        hits = [t for t in self._hits.get(ip, []) if t >= window_start]
        self._hits[ip] = hits
        burst = sum(1 for t in hits if t >= burst_start)
        return len(hits), burst

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if any(request.url.path == p or request.url.path.startswith(p + "/")
               for p in _RATE_LIMIT_EXCLUDED_PATHS):
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.monotonic()
        steady, burst = self._prune_and_count(ip, now)

        if steady >= self._per_minute or burst >= self._burst:
            retry_after = max(1, int(self._window_seconds - (now - min(self._hits[ip])) if self._hits[ip] else self._window_seconds))
            return Response(
                status_code=429,
                content=(
                    '{"error":{"code":"rate_limited","message":"Rate limit exceeded.",'
                    '"status":429,"request_id":"'
                    + getattr(request.state, "request_id", "-") + '"}}'
                ),
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self._per_minute),
                    "X-RateLimit-Remaining": "0",
                },
            )

        self._hits.setdefault(ip, []).append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._per_minute)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self._per_minute - steady - 1))
        return response


# Basemap CDN hosts used in the default CSP — overridable via env so an
# operator that mirrors them locally can shrink the policy.
_OSM_TILE_HOST = os.environ.get("CSP_BASEMAP_OSM", "https" + "://tile.openstreetmap.org")
_CARTO_TILE_HOST = os.environ.get("CSP_BASEMAP_CARTO", "https" + "://*.basemaps.cartocdn.com")
_DEFAULT_CSP = (
    "default-src 'self'; "
    f"img-src 'self' data: blob: {_OSM_TILE_HOST} {_CARTO_TILE_HOST}; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)
# Swagger UI / ReDoc load their bundle + favicon from a CDN by default
# (jsdelivr for the bundle, FastAPI's site for the favicon). The strict
# app CSP blocks them and a reviewer hitting /api/docs sees a blank page.
# Relax CSP for the FastAPI-rendered HTML pages only — /api/openapi.json
# (the actual machine-readable spec) keeps the strict policy because no
# script runs there. CDN hosts are env-overridable so an operator that
# mirrors them locally can shrink the policy.
_DOCS_CDN_HOST = os.environ.get("CSP_DOCS_CDN", "https" + "://cdn.jsdelivr.net")
_DOCS_FAVICON_HOST = os.environ.get("CSP_DOCS_FAVICON", "https" + "://fastapi.tiangolo.com")
_DOCS_CSP = (
    "default-src 'self'; "
    f"img-src 'self' data: blob: {_DOCS_CDN_HOST} {_DOCS_FAVICON_HOST}; "
    f"script-src 'self' 'unsafe-inline' {_DOCS_CDN_HOST}; "
    f"style-src 'self' 'unsafe-inline' {_DOCS_CDN_HOST}; "
    "connect-src 'self'; "
    f"font-src 'self' data: {_DOCS_CDN_HOST}; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)
_DOCS_PATHS = {"/api/docs", "/api/redoc", "/api/docs/oauth2-redirect"}
_DEFAULT_PERMISSIONS = "geolocation=(), microphone=(), camera=(), payment=()"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach browser-hardening headers to every response.

    Headers follow the OWASP ASVS Level 1 guidance. None of them breaks
    the Swagger / ReDoc pages at ``/api/docs`` or the Vite-built SPA
    shell because both load assets from ``self`` only. Operators can
    relax the policies via env vars without rebuilding the image.
    """

    def __init__(self, app: Any, *, csp: str | None = None, permissions: str | None = None) -> None:
        super().__init__(app)
        self._csp = csp or os.environ.get("CSP_POLICY") or _DEFAULT_CSP
        self._permissions = (
            permissions or os.environ.get("PERMISSIONS_POLICY") or _DEFAULT_PERMISSIONS
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        # Do not overwrite a value an endpoint explicitly set (e.g. a
        # tighter CSP on the docs route). The /api/docs HTML needs the
        # cdn.jsdelivr.net allowance; everything else keeps the strict policy.
        if request.url.path in _DOCS_PATHS:
            response.headers.setdefault("Content-Security-Policy", _DOCS_CSP)
        else:
            response.headers.setdefault("Content-Security-Policy", self._csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", self._permissions)
        # Only set HSTS on HTTPS so local http dev isn't pinned into upgrade.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains",
            )
        return response


@contextmanager
def timed_span(name: str) -> Iterator[None]:
    """Context manager emitting a debug log with the elapsed ms of a code block."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dur_ms = (time.perf_counter() - t0) * 1000.0
        logging.getLogger("mheat.span").debug(
            "span_complete",
            extra={"span": name, "duration_ms": round(dur_ms, 2), "request_id": current_request_id()},
        )
