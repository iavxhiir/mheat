"""Tests for the deep /api/readyz probe."""

from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from app.deps import settings_dep
from app.main import app


def test_readyz_with_full_substrate_is_ready(client):
    """conftest pre-populates sst.zarr + climatology.zarr → all checks pass."""
    r = client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    names = {c["name"] for c in body["checks"]}
    assert {"cache_dir_writable", "cms_credentials", "sst_cache",
            "climatology_artifact"} <= names
    assert all(c["ok"] for c in body["checks"])
    assert body["sst_cache_present"] is True
    assert body["climatology_present"] is True


def _live_settings(climatology_path, zarr_path, cache_dir,
                   creds=("u", "p")):
    """Build a Settings clone pinned to the supplied cache + climatology paths."""
    from app.config import Settings

    return Settings(
        COPERNICUSMARINE_SERVICE_USERNAME=creds[0],
        COPERNICUSMARINE_SERVICE_PASSWORD=creds[1],
        CLIMATOLOGY_STORE=climatology_path,
        cache_dir=cache_dir,
        zarr_store=zarr_path,
    )


def test_readyz_reports_missing_climatology(tmp_path):
    """Climatology absent → climatology_artifact check fails, 503."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    missing_clim = tmp_path / "does_not_exist.zarr"
    sst_path = tmp_path / "sst.zarr"
    # Create a sentinel SST cube so only the climatology probe fails.
    sst_path.mkdir()
    (sst_path / ".zgroup").write_text('{"zarr_format": 2}', encoding="utf-8")

    app.dependency_overrides[settings_dep] = lambda: _live_settings(
        missing_clim, sst_path, cache_dir,
    )
    try:
        live_client = TestClient(app)
        r = live_client.get("/api/readyz")
    finally:
        app.dependency_overrides.pop(settings_dep, None)

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["climatology_present"] is False
    failing = [c for c in body["checks"] if not c["ok"]]
    clim_check = next(c for c in failing if c["name"] == "climatology_artifact")
    assert "bootstrap_climatology" in (clim_check["detail"] or "")


def test_readyz_reports_missing_sst_cache(tmp_path):
    """SST cube absent → sst_cache check fails, 503."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    missing_sst = tmp_path / "no_sst.zarr"
    clim_path = tmp_path / "climatology.zarr"
    clim_path.mkdir()
    (clim_path / ".zgroup").write_text('{"zarr_format": 2}', encoding="utf-8")

    app.dependency_overrides[settings_dep] = lambda: _live_settings(
        clim_path, missing_sst, cache_dir,
    )
    try:
        live_client = TestClient(app)
        r = live_client.get("/api/readyz")
    finally:
        app.dependency_overrides.pop(settings_dep, None)

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["sst_cache_present"] is False
    failing = [c for c in body["checks"] if not c["ok"]]
    assert any(c["name"] == "sst_cache" for c in failing)


def test_readyz_reports_missing_cms_credentials(tmp_path):
    """No CMS creds → cms_credentials check fails, 503."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    sst_path = tmp_path / "sst.zarr"; sst_path.mkdir()
    (sst_path / ".zgroup").write_text('{"zarr_format": 2}', encoding="utf-8")
    clim_path = tmp_path / "climatology.zarr"; clim_path.mkdir()
    (clim_path / ".zgroup").write_text('{"zarr_format": 2}', encoding="utf-8")

    app.dependency_overrides[settings_dep] = lambda: _live_settings(
        clim_path, sst_path, cache_dir, creds=("", ""),
    )
    try:
        live_client = TestClient(app)
        r = live_client.get("/api/readyz")
    finally:
        app.dependency_overrides.pop(settings_dep, None)

    assert r.status_code == 503
    body = r.json()
    assert body["cms_credentials"] is False
    failing = [c for c in body["checks"] if not c["ok"]]
    assert any(c["name"] == "cms_credentials" for c in failing)


def test_readyz_top_level_fields_match_check_results(client):
    """The top-level booleans must agree with the per-check `ok` values."""
    body = client.get("/api/readyz").json()
    sst_check = next(c for c in body["checks"] if c["name"] == "sst_cache")
    clim_check = next(c for c in body["checks"] if c["name"] == "climatology_artifact")
    assert body["sst_cache_present"] == sst_check["ok"]
    assert body["climatology_present"] == clim_check["ok"]
