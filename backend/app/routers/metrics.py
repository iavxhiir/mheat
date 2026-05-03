"""`/api/metrics` — Prometheus scrape endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette.responses import Response

from .. import metrics

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get(
    "/metrics",
    summary="Prometheus metrics (if enabled)",
    response_class=Response,
    responses={
        200: {"content": {"text/plain": {}}, "description": "Prometheus exposition text."},
        404: {"description": "Metrics are disabled (set METRICS_ENABLED=true)."},
    },
)
def prometheus_metrics() -> Response:
    if not metrics.is_enabled():
        raise HTTPException(status_code=404, detail="metrics disabled")
    body, content_type = metrics.render_latest()
    return Response(content=body, media_type=content_type)
