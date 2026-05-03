"""Health and readiness endpoints."""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.responses import JSONResponse

from .. import metrics as _metrics
from ..config import Settings
from ..deps import settings_dep

router = APIRouter(prefix="/api", tags=["health"])

# Process start timestamp — captured at module import. Used by /readyz to
# expose ``uptime_seconds`` so an operator can correlate "service back up
# at HH:MM" with cluster events without grep'ing the access log.
_PROCESS_START_MONOTONIC = time.monotonic()


class HealthResponse(BaseModel):
    """Basic liveness response."""

    status: str
    version: str


class ReadyCheck(BaseModel):
    """One readiness probe — a named check with a boolean outcome."""

    name: str
    ok: bool
    detail: str | None = None


class ClimatologyInfo(BaseModel):
    """Best-effort climatology metadata exposed alongside the readiness probe.

    All fields default to ``None`` so a missing artifact still serialises
    cleanly. Populated from the on-disk Zarr's attrs when present.
    """

    clim_start: int | None = None
    clim_end: int | None = None
    age_days: float | None = None
    source_dataset: str | None = None


class ReadyResponse(BaseModel):
    """Readiness response — deep probes, one entry per check."""

    status: str  # "ready" | "degraded"
    version: str
    uptime_seconds: float
    cms_credentials: bool
    cache_dir: str
    zarr_store: str
    sst_cache_present: bool
    climatology_present: bool
    climatology: ClimatologyInfo | None = None
    checks: list[ReadyCheck]


def _probe_cache_writable(cache_dir: Path) -> ReadyCheck:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".readyz"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return ReadyCheck(name="cache_dir_writable", ok=True)
    except OSError as e:  # pragma: no cover — failure path exercised in tests
        return ReadyCheck(name="cache_dir_writable", ok=False, detail=str(e))


def _probe_cms_creds(settings: Settings) -> ReadyCheck:
    ok = settings.credentials_present()
    return ReadyCheck(
        name="cms_credentials",
        ok=ok,
        detail=None if ok else "COPERNICUSMARINE_SERVICE_USERNAME / PASSWORD not set",
    )


def _probe_sst_cache(settings: Settings) -> ReadyCheck:
    """The cached SST cube must exist for cached-date requests to succeed."""
    from ..cache import CacheStore

    cache = CacheStore(settings.cache_dir, settings.zarr_store)
    if cache.zarr_exists():
        return ReadyCheck(
            name="sst_cache",
            ok=True,
            detail=f"present at {settings.zarr_store}",
        )
    return ReadyCheck(
        name="sst_cache",
        ok=False,
        detail=(
            f"SST cube missing at {settings.zarr_store}; the startup prefetch "
            "will populate it once Copernicus credentials are configured."
        ),
    )


def _probe_climatology(settings: Settings) -> ReadyCheck:
    """The pre-computed Hobday climatology Zarr is required for /api/anomaly etc."""
    path = Path(settings.climatology_store)
    if path.exists():
        return ReadyCheck(
            name="climatology_artifact",
            ok=True,
            detail=f"present at {path}",
        )
    return ReadyCheck(
        name="climatology_artifact",
        ok=False,
        detail=(
            f"climatology missing at {path}; run "
            "scripts/bootstrap_climatology.py"
        ),
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Reports the service version. 200 OK whenever the process is responsive.",
    response_description="200 OK whenever the process is responsive",
)
def health() -> HealthResponse:
    """Return the service version; 200 OK whenever the process is up."""
    from .. import __version__

    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/readyz",
    summary="Readiness probe (deep)",
    description=(
        "Deep readiness probe. Returns **200** with ``status=ready`` when every "
        "check passes, **503** with ``status=degraded`` and the failing checks "
        "listed when any probe fails. Checks: cache-dir writability, CMS "
        "credentials, SST cube presence, Hobday climatology presence."
    ),
    response_description="Readiness envelope with per-check diagnostics",
    responses={
        200: {
            "model": ReadyResponse,
            "content": {
                "application/json": {
                    "example": {
                        "status": "ready",
                        "cms_credentials": True,
                        "cache_dir": "/data/cache",
                        "zarr_store": "/data/cache/sst.zarr",
                        "sst_cache_present": True,
                        "climatology_present": True,
                        "checks": [
                            {"name": "cache_dir_writable", "ok": True, "detail": None},
                            {"name": "cms_credentials", "ok": True, "detail": None},
                            {"name": "sst_cache", "ok": True,
                             "detail": "present at /data/cache/sst.zarr"},
                            {"name": "climatology_artifact", "ok": True,
                             "detail": "present at /data/cache/climatology.zarr"},
                        ],
                    }
                }
            },
        },
        503: {
            "model": ReadyResponse,
            "description": "One or more readiness checks failed",
            "content": {
                "application/json": {
                    "example": {
                        "status": "degraded",
                        "cms_credentials": True,
                        "cache_dir": "/data/cache",
                        "zarr_store": "/data/cache/sst.zarr",
                        "sst_cache_present": True,
                        "climatology_present": False,
                        "checks": [
                            {"name": "cache_dir_writable", "ok": True, "detail": None},
                            {"name": "cms_credentials", "ok": True, "detail": None},
                            {"name": "sst_cache", "ok": True,
                             "detail": "present at /data/cache/sst.zarr"},
                            {
                                "name": "climatology_artifact",
                                "ok": False,
                                "detail": (
                                    "climatology missing at /data/cache/climatology.zarr; "
                                    "run scripts/bootstrap_climatology.py"
                                ),
                            },
                        ],
                    }
                }
            },
        },
    },
)
def ready(settings: Settings = Depends(settings_dep)) -> JSONResponse:
    """Deep readiness probe — suitable as a Kubernetes readinessProbe."""
    from .. import __version__

    checks: list[ReadyCheck] = [
        _probe_cache_writable(Path(settings.cache_dir)),
        _probe_cms_creds(settings),
        _probe_sst_cache(settings),
        _probe_climatology(settings),
    ]

    sst_cache_present = Path(settings.zarr_store).exists()
    climatology_present = Path(settings.climatology_store).exists()

    clim_info = _climatology_info(settings) if climatology_present else None

    all_ok = all(c.ok for c in checks)
    body = ReadyResponse(
        status="ready" if all_ok else "degraded",
        version=__version__,
        uptime_seconds=round(time.monotonic() - _PROCESS_START_MONOTONIC, 3),
        cms_credentials=settings.credentials_present(),
        cache_dir=str(settings.cache_dir),
        zarr_store=str(settings.zarr_store),
        sst_cache_present=sst_cache_present,
        climatology_present=climatology_present,
        climatology=clim_info,
        checks=checks,
    )

    # Publish the climatology artifact age so operators can alert on stale
    # baselines. NaN signals "absent or unparsable".
    age = clim_info.age_days if clim_info and clim_info.age_days is not None else math.nan
    _metrics.set_climatology_age_days(age)

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content=body.model_dump(),
    )


def _climatology_age_days(settings: Settings) -> float:
    """Return age in days of the climatology artifact, or NaN if unavailable.

    Kept for backwards compatibility — :func:`_climatology_info` returns the
    same value plus the rest of the metadata in one shot.
    """
    info = _climatology_info(settings)
    return info.age_days if info and info.age_days is not None else math.nan


def _climatology_info(settings: Settings) -> ClimatologyInfo | None:
    """Read climatology Zarr attrs and surface them on /readyz.

    Returns ``None`` if the artifact can't be opened. Best-effort: any
    individual attribute may be missing without failing the rest.
    """
    try:
        from ..cache import CacheStore
        from ..sst import SSTProvider

        cache = CacheStore(settings.cache_dir, settings.zarr_store)
        provider = SSTProvider(settings=settings, cache=cache)
        clim = provider.load_climatology()
        if clim is None:
            return None
        attrs = clim.attrs or {}

        clim_start = attrs.get("clim_start")
        clim_end = attrs.get("clim_end")
        source = attrs.get("source_dataset")

        age_days: float | None = None
        created = attrs.get("created_utc")
        if created:
            try:
                ts = datetime.fromisoformat(str(created))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                delta = datetime.now(UTC) - ts
                age_days = round(delta.total_seconds() / 86400.0, 3)
            except (ValueError, TypeError):
                age_days = None

        return ClimatologyInfo(
            clim_start=int(clim_start) if clim_start is not None else None,
            clim_end=int(clim_end) if clim_end is not None else None,
            age_days=age_days,
            source_dataset=str(source) if source else None,
        )
    except Exception:  # noqa: BLE001
        return None
