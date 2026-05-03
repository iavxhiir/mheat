"""Live-data freshness endpoint + on-demand prefetch trigger.

* ``GET /api/freshness`` — returns the cube extent, age of the most-recent
  CMS pull, and the in-progress flag. Lets the dashboard render a "Live ·
  updated 3 min ago" badge with a colour that degrades from green
  (< 24 h since pull) to red (> 72 h).

* ``POST /api/prefetch`` — fires an async ``SSTProvider.load_range(start,
  end)`` in a background task. Returns ``202 Accepted`` immediately so
  the frontend can fire-and-forget on every range click without blocking
  the events query that follows. The actual data lands in the on-disk
  cube; subsequent ``/api/events`` queries hit warm cache.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ..deps import sst_dep
from ..sst import SSTProvider, get_live_pull_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["freshness"])


@router.get(
    "/freshness",
    summary="Live-data freshness snapshot",
    description=(
        "Returns the cached SST cube extent, the timestamp of the most "
        "recent successful CMS pull, age in seconds, and whether a pull "
        "is currently in progress. The dashboard polls this to render a "
        "colour-coded freshness badge."
    ),
)
def freshness(sst: SSTProvider = Depends(sst_dep)) -> dict[str, Any]:
    """Return the freshness snapshot for /api/freshness."""
    state = get_live_pull_state()
    extent = sst.cube_extent()
    cube_end = extent[1].isoformat() if extent else None
    cube_start = extent[0].isoformat() if extent else None

    last_success_iso = state.get("last_success_at")
    age_seconds: int | None = None
    if isinstance(last_success_iso, str):
        try:
            ts = datetime.fromisoformat(last_success_iso.replace("Z", "+00:00"))
            age_seconds = int((datetime.now(timezone.utc) - ts).total_seconds())
        except ValueError:
            age_seconds = None

    return {
        "cube_start": cube_start,
        "cube_end": cube_end,
        "last_pull": {
            "in_progress": bool(state.get("in_progress")),
            "started_at": state.get("started_at"),
            "started_for_range": (
                {"start": state.get("start_date"), "end": state.get("end_date")}
                if state.get("start_date") else None
            ),
            "last_success_at": last_success_iso,
            "age_seconds": age_seconds,
            "last_error_at": state.get("last_error_at"),
            "last_error": state.get("last_error"),
        },
        # Front-end colour bucket — keep the source of truth here so every
        # client (dashboard, QGIS plugin, CLI) shows the same green / amber /
        # red interpretation of "fresh enough".
        "bucket": _bucket_for_age(age_seconds),
    }


def _bucket_for_age(age_seconds: int | None) -> str:
    """Categorise the staleness of the most recent pull."""
    if age_seconds is None:
        return "unknown"   # never pulled in this process
    if age_seconds < 6 * 3600:
        return "fresh"     # < 6 h
    if age_seconds < 24 * 3600:
        return "good"      # < 1 day
    if age_seconds < 72 * 3600:
        return "stale"     # 1-3 days
    return "very_stale"    # > 3 days


@router.post(
    "/prefetch",
    status_code=202,
    summary="Trigger an async CMS pull for a date range",
    description=(
        "Fire-and-forget endpoint that schedules a background "
        "`SSTProvider.load_range(start, end)` call. The dashboard hits "
        "this on every range / preset click so the events query that "
        "follows lands on warm cache. Returns 202 immediately with the "
        "queued range; poll /api/freshness to watch progress."
    ),
)
async def prefetch(
    background_tasks: BackgroundTasks,
    start: date = Query(..., description="YYYY-MM-DD inclusive"),
    end: date = Query(..., description="YYYY-MM-DD inclusive"),
    sst: SSTProvider = Depends(sst_dep),
) -> dict[str, Any]:
    """Schedule a background SST cache fill for `[start, end]`."""
    if end < start:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_range", "detail": "end must be >= start"},
        )
    # Cap the prefetch window so a typo doesn't spawn a multi-year CMS pull.
    if (end - start).days > 366:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "range_too_wide",
                "detail": "Prefetch range capped at 366 days; chunk larger ranges.",
            },
        )

    def _run() -> None:
        try:
            sst.load_range(start, end)
        except Exception as exc:  # noqa: BLE001
            # Already logged inside _fetch_and_merge; nothing more to do.
            logger.info("prefetch background task failed: %s", exc)

    background_tasks.add_task(_run)
    return {
        "status": "queued",
        "start": str(start),
        "end": str(end),
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


# Optional in-process periodic refresher. Off by default; enable with
# `LIVE_AUTO_REFRESH=true`. Pulls a small rolling window every
# `LIVE_REFRESH_HOURS` hours so the cube stays current without an
# external cron. Useful for single-replica EDITO Datalab deployments.
async def background_refresher_loop(
    sst: SSTProvider, interval_hours: float = 6.0,
) -> None:
    """Background coroutine — periodic cache refresh."""
    interval_s = max(60.0, interval_hours * 3600)
    while True:
        try:
            await asyncio.sleep(interval_s)
            today = date.today()
            from datetime import timedelta as _td
            sst.load_range(today - _td(days=2), today)
            logger.info("Background refresher pulled rolling 2-day window")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Background refresher iteration failed: %s", exc)
