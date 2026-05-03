"""OGC API — Processes 1.0 endpoints (sync + async)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..cache import CacheStore
from ..config import Settings
from ..deps import cache_dep, settings_dep, sst_dep
from ..impact import compute_impact
from ..metrics import inc_events_detected, observe_stage
from ..mhw import detect_cube, events_to_geojson, filter_events
from ..overlays import OverlayProvider
from ..processes import CONFORMANCE_CLASSES, JOB_STORE, run_async
from ..sst import CMSCredentialsMissingError, SSTProvider
from ._caching import json_with_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/processes", tags=["processes"])

# Process descriptor + conformance change at deploy time only.
_PROCESSES_STATIC_MAX_AGE = 300


class DetectInputs(BaseModel):
    """Input payload for the mhw-detect process."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "bbox": [12.0, 40.0, 20.0, 46.0],
                    "start": "2022-07-01",
                    "end": "2022-08-31",
                    "min_category": 3,
                    "with_impact": True,
                },
                {
                    "bbox": [-6.0, 30.0, 37.0, 46.0],
                    "start": "2024-07-01",
                    "end": "2024-08-31",
                    "min_category": 1,
                    "with_impact": False,
                },
            ],
        }
    )

    bbox: list[float] | None = Field(
        default=None,
        description="[lon_min, lat_min, lon_max, lat_max]",
        min_length=4,
        max_length=4,
    )
    start: date | None = None
    end: date | None = None
    min_category: int = Field(default=1, ge=1, le=5)
    with_impact: bool = Field(default=True, description="Compute overlay intersections")


class ExecuteBody(BaseModel):
    """OGC API — Processes execute body: ``{"inputs": {...}, "response": "..."}``."""

    inputs: DetectInputs
    response: Literal["document", "raw"] = "document"


class DetectResponse(BaseModel):
    """Sync-execute response envelope."""

    status: str
    n_events: int
    events: dict[str, Any]
    impact: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Process description + conformance
# ---------------------------------------------------------------------------
_PROCESS_DESCRIPTOR: dict[str, Any] = {
    "id": "mhw-detect",
    "title": "Mediterranean Marine Heatwave Detection (Hobday 2016)",
    "version": "0.2.0",
    "description": (
        "Runs the Hobday et al. (2016) pixel-wise MHW detector on a "
        "configurable bbox + time window, optionally joining the detected "
        "events with EMODnet sectoral overlays."
    ),
    "jobControlOptions": ["sync-execute", "async-execute"],
    "outputTransmission": ["value"],
    "inputs": {
        "bbox": {"title": "Bounding box",
                 "schema": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}},
        "start": {"title": "Start date", "schema": {"type": "string", "format": "date"}},
        "end": {"title": "End date", "schema": {"type": "string", "format": "date"}},
        "min_category": {"title": "Minimum Hobday 2018 category",
                         "schema": {"type": "integer", "minimum": 1, "maximum": 5}},
        "with_impact": {"title": "Attach sectoral-impact overlays",
                        "schema": {"type": "boolean"}},
    },
    "outputs": {
        "events": {"schema": {"type": "object", "description": "GeoJSON FeatureCollection"}},
        "impact": {"schema": {"type": "object", "description": "Per-event impact summary"}},
    },
}


@router.get(
    "",
    summary="List available processes",
    description="OGC API — Processes listing of the computation jobs MHEAT exposes.",
)
def list_processes(request: Request) -> Response:
    return json_with_cache(
        request, {"processes": [_PROCESS_DESCRIPTOR]}, max_age=_PROCESSES_STATIC_MAX_AGE,
    )


@router.get(
    "/conformance",
    summary="OGC API — Processes 1.0 conformance classes",
)
def conformance(request: Request) -> Response:
    return json_with_cache(
        request, {"conformsTo": list(CONFORMANCE_CLASSES)}, max_age=_PROCESSES_STATIC_MAX_AGE,
    )


# Declare the literal ``/jobs*`` routes BEFORE the dynamic
# ``/{process_id}`` one below — Starlette matches in declaration order and
# would otherwise route ``/jobs`` through the descriptor handler.
@router.get("/jobs", summary="List recent jobs")
def list_jobs_route() -> dict[str, Any]:
    return {"jobs": [j.to_status_info() for j in JOB_STORE.list_jobs()]}


@router.get("/jobs/{job_id}", summary="Job status (OGC API — Processes statusInfo)")
def get_job_route(
    job_id: str = Path(..., description="Opaque job id returned by ``POST /execution``"),
) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_status_info()


@router.get(
    "/jobs/{job_id}/results",
    summary="Job results",
    description=(
        "Returns the results of a completed job. **404** if the job id is "
        "unknown or the job hasn't finished yet."
    ),
)
def get_job_results_route(
    job_id: str = Path(..., description="Opaque job id returned by ``POST /execution``"),
) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "failed":
        raise HTTPException(status_code=500, detail=job.error or {"message": "job failed"})
    if job.status != "successful" or job.result is None:
        raise HTTPException(status_code=404, detail=f"Job not yet complete (status={job.status})")
    return job.result


@router.delete(
    "/jobs/{job_id}",
    summary="Dismiss a job",
    description="Marks the job as dismissed. Does not cancel a running worker.",
)
def dismiss_job_route(
    job_id: str = Path(..., description="Opaque job id returned by ``POST /execution``"),
) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    JOB_STORE.update(job_id, status="dismissed", message="Dismissed by client.")
    dismissed = JOB_STORE.get(job_id)
    assert dismissed is not None  # noqa: S101
    return dismissed.to_status_info()


@router.get(
    "/{process_id}",
    summary="Process descriptor",
    description=(
        "OGC API — Processes 1.0 process-description document. Stable for "
        "the lifetime of the deployment so it carries a 5-minute "
        "``Cache-Control`` and a strong ``ETag``."
    ),
)
def describe_process(
    request: Request,
    process_id: str = Path(
        ..., description="Process id (currently only `mhw-detect`)",
    ),
) -> Response:
    if process_id != "mhw-detect":
        raise HTTPException(status_code=404, detail="Process not found")
    return json_with_cache(request, _PROCESS_DESCRIPTOR, max_age=_PROCESSES_STATIC_MAX_AGE)


# ---------------------------------------------------------------------------
# Core detection routine — shared between sync and async paths.
# ---------------------------------------------------------------------------
def _run_detect(
    inputs: DetectInputs,
    settings: Settings,
    sst: SSTProvider,
    cache: CacheStore,
) -> dict[str, Any]:
    """Synchronous detection — returns ``{n_events, events, impact}``."""
    # Fail-fast on missing climatology BEFORE the (expensive) CMS subset
    # call: a stale or absent artifact is the most common misconfiguration
    # and we'd rather a 503 in milliseconds than minutes.
    baseline = sst.load_climatology()
    if baseline is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "climatology_missing",
                "detail": (
                    "Run scripts/bootstrap_climatology.py before using "
                    "/api/processes/mhw-detect"
                ),
                "climatology_store": str(settings.climatology_store),
            },
        )

    if inputs.start is None or inputs.end is None:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "dates_required",
                "detail": "start and end inputs are required",
            },
        )
    try:
        ds = sst.load_range(inputs.start, inputs.end)
    except CMSCredentialsMissingError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    var_name = next(
        (v for v in ("analysed_sst", "sst", "thetao") if v in ds.data_vars),
        None,
    )
    if var_name is None:
        raise HTTPException(status_code=500, detail="No SST variable in dataset")

    sst_da = ds[var_name]
    rename = {}
    if "lat" in sst_da.dims and "latitude" not in sst_da.dims:
        rename["lat"] = "latitude"
    if "lon" in sst_da.dims and "longitude" not in sst_da.dims:
        rename["lon"] = "longitude"
    if rename:
        sst_da = sst_da.rename(rename)

    with observe_stage("detect"):
        events = detect_cube(
            sst_da,
            clim_period=(settings.clim_start, settings.clim_end),
            baseline=baseline,
        )

    with observe_stage("cluster"):
        bbox_tuple: tuple[float, float, float, float] | None = (
            (inputs.bbox[0], inputs.bbox[1], inputs.bbox[2], inputs.bbox[3])
            if inputs.bbox and len(inputs.bbox) == 4
            else None
        )
        events = filter_events(events, start=inputs.start, end=inputs.end, bbox=bbox_tuple)
        events = [e for e in events if e.category >= inputs.min_category]

    inc_events_detected(len(events))
    geojson = events_to_geojson(events)

    impact_payload: dict[str, Any] | None = None
    if inputs.with_impact:
        with observe_stage("impact"):
            provider = OverlayProvider(settings=settings, cache=cache)
            overlays = {kind: provider.get(kind) for kind in ("aquaculture", "mpa", "seagrass")}
            impact_payload = compute_impact(events, overlays)

    return {
        "n_events": len(events),
        "events": geojson,
        "impact": impact_payload,
    }


# ---------------------------------------------------------------------------
# Legacy sync endpoint (kept for early clients).
# ---------------------------------------------------------------------------
@router.post(
    "/mhw-detect",
    response_model=DetectResponse,
    summary="Run MHW detection (sync, legacy path)",
    description=(
        "Legacy sync-execute endpoint. Prefer "
        "``POST /api/processes/mhw-detect/execution`` for full OGC API — "
        "Processes 1.0 conformance."
    ),
)
def mhw_detect(
    inputs: DetectInputs,
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> DetectResponse:
    result = _run_detect(inputs, settings, sst, cache)
    return DetectResponse(status="successful", **result)


# ---------------------------------------------------------------------------
# OGC API — Processes 1.0 execute + jobs
# ---------------------------------------------------------------------------
@router.post(
    "/mhw-detect/execution",
    summary="Execute the mhw-detect process (OGC API — Processes 1.0)",
    description=(
        "Canonical OGC API — Processes 1.0 execute endpoint. Without "
        "``Prefer: respond-async`` the call runs synchronously and returns "
        "``200`` with the results inline. With that header the call returns "
        "``201`` + a ``Location`` header pointing at the status endpoint."
    ),
    responses={
        200: {"description": "Synchronous execute completed."},
        201: {"description": "Async execute accepted; poll the Location header."},
    },
)
def execute_mhw_detect(
    body: ExecuteBody = Body(...),
    prefer: str | None = Header(
        None, alias="Prefer",
        description="Set to ``respond-async`` for async execution (returns 201 + Location).",
    ),
    settings: Settings = Depends(settings_dep),
    sst: SSTProvider = Depends(sst_dep),
    cache: CacheStore = Depends(cache_dep),
) -> Response:
    want_async = prefer is not None and "respond-async" in prefer.lower()

    if want_async:
        job = JOB_STORE.create("mhw-detect")
        inputs = body.inputs
        run_async(
            JOB_STORE,
            job.job_id,
            lambda: _run_detect(inputs, settings, sst, cache),
        )
        return JSONResponse(
            status_code=201,
            content=job.to_status_info(),
            headers={"Location": f"/api/processes/jobs/{job.job_id}"},
        )

    result = _run_detect(body.inputs, settings, sst, cache)
    if body.response == "raw":
        return JSONResponse(status_code=200, content=result["events"])
    return JSONResponse(status_code=200, content={"status": "successful", **result})


# /jobs routes are declared above — before the dynamic /{process_id} route.
