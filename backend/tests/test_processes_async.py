"""Tests for the OGC API — Processes 1.0 async job lifecycle."""

from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _clean_jobs():
    from app.processes import JOB_STORE
    JOB_STORE.clear()
    yield
    JOB_STORE.clear()


EXECUTE_BODY = {
    "inputs": {"start": "2022-07-01", "end": "2022-08-15", "min_category": 3, "with_impact": False},
    "response": "document",
}


def test_processes_listing_advertises_async_jobcontrol(client):
    r = client.get("/api/processes")
    descriptor = r.json()["processes"][0]
    assert "async-execute" in descriptor["jobControlOptions"]
    assert "sync-execute" in descriptor["jobControlOptions"]


def test_conformance_endpoint_lists_ogcapi_processes_classes(client):
    r = client.get("/api/processes/conformance")
    assert r.status_code == 200
    classes = r.json().get("conformsTo", [])
    assert any("ogcapi-processes-1/1.0/conf/core" in c for c in classes)


def test_describe_process_404_for_unknown(client):
    r = client.get("/api/processes/does-not-exist")
    assert r.status_code == 404


def test_sync_execute_returns_200_with_events(client):
    r = client.post("/api/processes/mhw-detect/execution", json=EXECUTE_BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "successful"
    assert "events" in body and body["events"]["type"] == "FeatureCollection"


def test_sync_execute_raw_response_returns_only_the_geojson(client):
    r = client.post(
        "/api/processes/mhw-detect/execution",
        json={**EXECUTE_BODY, "response": "raw"},
    )
    assert r.status_code == 200
    body = r.json()
    # Raw-mode body is the FeatureCollection itself, no envelope.
    assert body["type"] == "FeatureCollection"
    assert "status" not in body


def test_async_execute_returns_201_with_location_header(client):
    r = client.post(
        "/api/processes/mhw-detect/execution",
        json=EXECUTE_BODY,
        headers={"Prefer": "respond-async"},
    )
    assert r.status_code == 201
    loc = r.headers["Location"]
    assert loc.startswith("/api/processes/jobs/")
    info = r.json()
    assert info["processID"] == "mhw-detect"
    assert info["status"] in {"accepted", "running", "successful"}


def test_async_job_eventually_succeeds_and_results_are_fetched(client):
    r = client.post(
        "/api/processes/mhw-detect/execution",
        json=EXECUTE_BODY,
        headers={"Prefer": "respond-async"},
    )
    job_id = r.headers["Location"].rsplit("/", 1)[-1]

    # Poll up to 120 s — the demo cube usually finishes in < 1 s but can
    # stretch under a loaded CI runner.
    final_status = ""
    for _ in range(240):
        status = client.get(f"/api/processes/jobs/{job_id}").json()
        final_status = status["status"]
        if final_status in {"successful", "failed", "dismissed"}:
            break
        time.sleep(0.5)
    assert final_status == "successful", f"job did not succeed: {final_status}"

    results = client.get(f"/api/processes/jobs/{job_id}/results")
    assert results.status_code == 200
    body = results.json()
    assert "events" in body and body["events"]["type"] == "FeatureCollection"


def test_job_results_404_for_unknown_job(client):
    r = client.get("/api/processes/jobs/not-a-real-id/results")
    assert r.status_code == 404


def test_job_results_404_while_job_is_still_running(client):
    # Create a job and immediately ask for results before the worker finishes.
    from app.processes import JOB_STORE

    job = JOB_STORE.create("mhw-detect")
    r = client.get(f"/api/processes/jobs/{job.job_id}/results")
    assert r.status_code == 404
    body = r.json()
    # Error envelope wraps the detail; message should mention job state.
    assert "not yet complete" in body["error"]["message"].lower()


def test_list_jobs_includes_recently_created(client):
    from app.processes import JOB_STORE
    JOB_STORE.create("mhw-detect")
    JOB_STORE.create("mhw-detect")
    r = client.get("/api/processes/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) >= 2


def test_dismiss_job_marks_status_and_returns_statusinfo(client):
    from app.processes import JOB_STORE
    job = JOB_STORE.create("mhw-detect")
    r = client.delete(f"/api/processes/jobs/{job.job_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"


def test_dismiss_unknown_job_is_404(client):
    r = client.delete("/api/processes/jobs/abc123-unknown")
    assert r.status_code == 404
