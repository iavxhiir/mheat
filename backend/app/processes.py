"""OGC API — Processes 1.0 Core — async job registry.

Routers expose three endpoints per the spec:

* ``POST /api/processes/{id}/execution`` — execute (sync or async via
  ``Prefer: respond-async`` header).
* ``GET  /api/processes/jobs/{jobId}``   — status info.
* ``GET  /api/processes/jobs/{jobId}/results`` — final output.

This module owns the **in-process** job registry. For a multi-replica
EDITO deployment, swap it for a shared store (Redis / Postgres). The
public contract is the :class:`JobStore` interface.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

JobStatus = Literal["accepted", "running", "successful", "failed", "dismissed"]


@dataclass
class Job:
    """One tracked execution of a registered process."""

    job_id: str
    process_id: str
    status: JobStatus = "accepted"
    created: datetime = field(default_factory=lambda: datetime.now(UTC))
    started: datetime | None = None
    finished: datetime | None = None
    progress: int = 0
    message: str = "Queued"
    # Populated when status == 'successful'.
    result: dict[str, Any] | None = None
    # Populated when status == 'failed'.
    error: dict[str, Any] | None = None

    def to_status_info(self) -> dict[str, Any]:
        """Render the OGC API — Processes `statusInfo` payload."""
        payload: dict[str, Any] = {
            "jobID": self.job_id,
            "processID": self.process_id,
            "status": self.status,
            "created": self.created.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "links": [
                {
                    "rel": "self",
                    "type": "application/json",
                    "href": f"/api/processes/jobs/{self.job_id}",
                },
            ],
        }
        if self.started:
            payload["started"] = self.started.isoformat()
        if self.finished:
            payload["finished"] = self.finished.isoformat()
        if self.status == "successful":
            payload["links"].append({
                "rel": "http://www.opengis.net/def/rel/ogc/1.0/results",
                "type": "application/json",
                "href": f"/api/processes/jobs/{self.job_id}/results",
            })
        return payload


class JobStore:
    """Thread-safe in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, process_id: str) -> Job:
        job = Job(job_id=uuid.uuid4().hex, process_id=process_id)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 100) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)[:limit]

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in fields.items():
                setattr(job, k, v)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


# Module-level singleton — a real deployment swaps this for a shared store.
JOB_STORE = JobStore()


def run_async(
    store: JobStore,
    job_id: str,
    fn: Callable[[], dict[str, Any]],
) -> None:
    """Execute ``fn`` in a worker thread, piping status updates into ``store``.

    ``fn`` must be synchronous and return a JSON-serialisable dict (the
    process ``results``). Exceptions are captured as job failures, never
    surfaced to the caller — the async caller has already received its 201.
    """

    def _worker() -> None:
        store.update(job_id, status="running", started=datetime.now(UTC),
                     progress=5, message="Execution started.")
        t0 = time.perf_counter()
        try:
            result = fn()
            store.update(
                job_id,
                status="successful",
                finished=datetime.now(UTC),
                progress=100,
                message=f"Completed in {time.perf_counter() - t0:.2f} s.",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Async job %s failed", job_id)
            store.update(
                job_id,
                status="failed",
                finished=datetime.now(UTC),
                progress=100,
                message=f"Execution failed: {exc}",
                error={"message": str(exc), "type": exc.__class__.__name__},
            )

    thread = threading.Thread(target=_worker, name=f"mhw-job-{job_id[:8]}", daemon=True)
    thread.start()


CONFORMANCE_CLASSES: tuple[str, ...] = (
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/ogc-process-description",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/json",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/oas30",
)
