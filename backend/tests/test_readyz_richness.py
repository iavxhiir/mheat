"""Pass-84 polish — `/api/readyz` exposes operator-debug fields.

The deep readiness probe used to surface only the per-check booleans plus
``cache_dir`` / ``zarr_store``. Pass 84 added:

* ``version``         — service version (matches ``/api/health``).
* ``uptime_seconds``  — process uptime, monotonic.
* ``climatology``     — block with ``clim_start``, ``clim_end``,
  ``age_days``, ``source_dataset`` derived from the on-disk Zarr attrs.

These let an operator triage from the probe alone without grep'ing logs
or shelling into the container.
"""

from __future__ import annotations


def test_readyz_exposes_version(client):
    body = client.get("/api/readyz").json()
    assert "version" in body
    assert isinstance(body["version"], str) and body["version"]
    # Must agree with /api/health's view of the version.
    assert body["version"] == client.get("/api/health").json()["version"]


def test_readyz_exposes_uptime_seconds(client):
    body = client.get("/api/readyz").json()
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0


def test_readyz_includes_climatology_metadata_when_present(client):
    """The conftest pre-populates a 1991-2020 climatology — surface its attrs."""
    body = client.get("/api/readyz").json()
    assert body["climatology_present"] is True
    info = body.get("climatology")
    assert info is not None, "climatology block missing despite climatology_present"
    assert info["clim_start"] == 1991
    assert info["clim_end"] == 2020
    # source_dataset is set in the test fixture.
    assert info["source_dataset"] == "synthetic-test-fixture"
