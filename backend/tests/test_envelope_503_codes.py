"""503 envelope shape: stable codes for ``climatology_missing`` & friends.

The remediation-style 5xx errors raised by ``/api/events`` and
``/api/anomaly`` carry a dict ``detail`` like::

    {"status": "climatology_missing", "detail": "...", "climatology_store": "..."}

After pass 84 those flow through the canonical error envelope with
``error.code == "climatology_missing"`` (not the generic
``service_unavailable``) so clients can switch on the slug. The dict
``detail`` is preserved verbatim under ``error.detail`` so existing
integrations keep working.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import settings_dep
from app.main import app


@pytest.fixture()
def _missing_clim_client(tmp_path):
    """Spin up a TestClient pointing at a populated cache + missing climatology."""
    cache_dir = tmp_path / "cache"
    sst_path = cache_dir / "sst.zarr"
    cache_dir.mkdir(parents=True)
    sst_path.mkdir()
    (sst_path / ".zgroup").write_text('{"zarr_format": 2}', encoding="utf-8")
    missing_clim = tmp_path / "no-clim.zarr"  # never created

    def _ovr() -> Settings:
        return Settings(
            CACHE_DIR=cache_dir,
            ZARR_STORE=sst_path,
            CLIMATOLOGY_STORE=missing_clim,
            COPERNICUSMARINE_SERVICE_USERNAME="ci",
            COPERNICUSMARINE_SERVICE_PASSWORD="ci",
        )

    app.dependency_overrides[settings_dep] = _ovr
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(settings_dep, None)
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_anomaly_climatology_missing_has_canonical_envelope(_missing_clim_client):
    r = _missing_clim_client.get("/api/anomaly?date=2022-07-20")
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "climatology_missing"
    assert body["error"]["status"] == 503
    # The remediation payload is preserved verbatim under ``error.detail``.
    detail = body["error"]["detail"]
    assert detail["status"] == "climatology_missing"
    assert "bootstrap_climatology" in detail["detail"]
    assert "climatology_store" in detail


def test_events_climatology_missing_has_canonical_envelope(_missing_clim_client):
    r = _missing_clim_client.get("/api/events?start=2022-07-01&end=2022-08-15")
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "climatology_missing"
    detail = body["error"]["detail"]
    assert detail["status"] == "climatology_missing"


def test_anomaly_503_no_longer_returns_raw_status_payload(_missing_clim_client):
    """Regression — pre-pass-84 the 503 used to bypass the envelope."""
    body = _missing_clim_client.get("/api/anomaly?date=2022-07-20").json()
    # The legacy raw shape would have ``status`` at the top level. We assert
    # the shape is now the wrapped envelope so a future regression trips.
    assert "error" in body
    assert "status" not in body  # only inside body["error"]["detail"]
