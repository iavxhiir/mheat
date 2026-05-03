"""FastAPI entry point for MHEAT.

Structure:

* ``/api/*``  — REST + STAC endpoints.
* ``/``       — built Vite frontend (only when ``FRONTEND_DIR`` exists).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .auth import configure_oidc
from .config import get_settings
from .errors import register_error_handlers
from .logging_config import configure_logging
from .metrics import MetricsMiddleware, init_metrics
from .middleware import (
    AccessLogMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .routers import (
    aggregate,
    anomaly,
    data,
    detect,
    events,
    freshness,
    health,
    ogcapi,
    overlays,
    sectoral,
    stac,
)
from .routers import metrics as metrics_router
from .telemetry import init_otel

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: D401
    """Startup / shutdown hooks."""
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "MHEAT v%s starting (bbox=%s, cache=%s)",
        __version__, settings.bbox, settings.cache_dir,
    )
    if not settings.credentials_present():
        logger.warning(
            "Copernicus Marine credentials are not set — endpoints that need "
            "an uncached date will return 503 until COPERNICUSMARINE_SERVICE_"
            "USERNAME / _PASSWORD are provided."
        )

    # Warm the cube with the last 90 days of NRT so the default UI view
    # renders entirely from disk. Failure here is non-fatal: the request
    # path will return clear 503s if the cache is empty when hit.
    try:
        from .cache import CacheStore
        from .sst import SSTProvider
        cache = CacheStore(settings.cache_dir, settings.zarr_store)
        provider = SSTProvider(settings=settings, cache=cache)
        if provider.prefetch_warm_window():
            logger.info("Startup prefetch complete")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Startup prefetch failed: %s", exc)

    yield
    logger.info("MHEAT shutting down")


_TAGS_METADATA = [
    {
        "name": "health",
        "description": "Liveness and readiness probes suitable for Kubernetes.",
    },
    {
        "name": "events",
        "description": (
            "Detected marine heatwave events as GeoJSON (clustered or raw) "
            "and as CSV, plus per-event time-series diagnostics."
        ),
    },
    {
        "name": "overlays",
        "description": "Sectoral GeoJSON overlays: aquaculture sites, MPAs, seagrass beds.",
    },
    {
        "name": "stac",
        "description": "STAC catalog describing the MHEAT output artefacts.",
    },
    {
        "name": "anomaly",
        "description": "PNG rasters of the SST anomaly for a given date.",
    },
    {
        "name": "data",
        "description": (
            "ARCO data assets — Zarr stores served as static byte ranges so "
            "remote xarray clients can stream SST and climatology cubes."
        ),
    },
    {
        "name": "processes",
        "description": "OGC-API-Processes-style compute endpoints.",
    },
    {
        "name": "ogcapi",
        "description": "OGC API — Features 1.0 endpoints (QGIS/ArcGIS-compatible).",
    },
    {
        "name": "metrics",
        "description": "Prometheus scrape endpoint (opt-in via METRICS_ENABLED).",
    },
    {
        "name": "sectoral",
        "description": (
            "Sector-specific helpers: per-farm exposure, per-MPA history, and "
            "a minimal OGC WMS 1.3 GetMap wrapper for desktop GIS clients."
        ),
    },
]


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    app = FastAPI(
        title="MHEAT API",
        version=__version__,
        description=(
            "**MHEAT** — Mediterranean marine-heatwave detection & sectoral-impact API.\n\n"
            "Detects Mediterranean & Adriatic marine heatwaves on Copernicus "
            "Marine SST using the Hobday et al. (2016) method and exposes them "
            "as GeoJSON, CSV, a STAC catalog and PNG anomaly rasters for the "
            "EDITO platform. Reads from a local Zarr cache populated at boot "
            "(last 90 days of NRT) and lazy-filled on demand from CMS."
        ),
        contact={
            "name": "MHEAT maintainers",
            "url": "https://github.com/your-org/mheat",
        },
        license_info={
            "name": "MIT",
            "url": "https://opensource.org/licenses/MIT",
        },
        openapi_tags=_TAGS_METADATA,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Gzip everything > 1 KB. The big wins are on the overlay payloads
    # (MPA ~51 MB, seagrass ~13 MB) which compress 4-7× and turn
    # multi-second downloads into sub-second ones — critical for the
    # reviewer's first-impression latency. Smaller payloads are skipped
    # by the threshold so the per-request overhead is invisible.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    # Outermost request id, then access log, then security headers.
    # Order matters: request-id must run first so the log middleware can
    # pick the id up from request.state. Metrics (if enabled) is innermost
    # so it sees the resolved route template.
    # RequestSizeLimitMiddleware is outermost so a gigabyte body hits the
    # 413 wall before any other work happens.
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    # Prometheus metrics (no-op if METRICS_ENABLED is unset).
    if init_metrics():
        app.add_middleware(MetricsMiddleware)

    # Opt-in rate limiter — default off so single-replica dev stays simple.
    if os.environ.get("RATE_LIMIT_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
        app.add_middleware(RateLimitMiddleware)

    # Opt-in OIDC — no-op unless OIDC_ISSUER is set at startup.
    configure_oidc(app)

    # OpenTelemetry (no-op if OTEL_EXPORTER_OTLP_ENDPOINT is unset).
    init_otel(app)

    # API routers
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(detect.router)
    app.include_router(overlays.router)
    app.include_router(stac.router)
    app.include_router(anomaly.router)
    app.include_router(data.router)
    app.include_router(ogcapi.router)
    app.include_router(freshness.router)
    app.include_router(aggregate.router)
    app.include_router(sectoral.router)
    app.include_router(metrics_router.router)

    # Uniform JSON error envelope across HTTPException / ValidationError / uncaught.
    register_error_handlers(app)

    # --- Frontend static serving ---
    frontend_dir = Path(os.environ.get("FRONTEND_DIR", settings.frontend_dir))
    if frontend_dir.exists() and (frontend_dir / "index.html").exists():
        logger.info("Serving frontend from %s", frontend_dir)

        # Serve Vite /assets/* directly
        assets_dir = frontend_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def _root() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

        # SPA fallback — any non-/api path returns index.html so client-side
        # routing still works for deep links.
        from fastapi import HTTPException

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                # Route through the error-envelope handler so API clients
                # always see the canonical {"error": {...}} shape.
                raise HTTPException(status_code=404, detail="Not Found")
            target = frontend_dir / full_path
            if target.is_file():
                return FileResponse(target)
            return FileResponse(frontend_dir / "index.html")
    else:
        logger.info("No frontend build found at %s — API-only mode", frontend_dir)

        @app.get("/", include_in_schema=False)
        async def _api_only_root() -> JSONResponse:
            return JSONResponse(
                {
                    "service": "mheat",
                    "version": __version__,
                    "docs": "/api/docs",
                    "frontend": "not_built",
                }
            )

    return app


app = create_app()
