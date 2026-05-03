"""Tests covering the SPA / static-assets mount in `app.main`.

The regular test suite runs with ``FRONTEND_DIR`` pointing at a
non-existent path (see ``conftest.py``). To reach the mounted-SPA
branches we boot a second app with ``FRONTEND_DIR`` pointing at a
throwaway directory holding a minimal ``index.html`` + ``assets/``.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def spa_client(tmp_path):
    """Boot a fresh app with FRONTEND_DIR aimed at a real mini-SPA tree."""
    frontend = tmp_path / "spa"
    assets = frontend / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text(
        "<!doctype html><html><body>MHEAT SPA shell</body></html>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("// fake bundled app\n", encoding="utf-8")
    (frontend / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")

    previous = os.environ.get("FRONTEND_DIR")
    os.environ["FRONTEND_DIR"] = str(frontend)
    try:
        from app.config import get_settings
        get_settings.cache_clear()
        import app.main as main_mod
        importlib.reload(main_mod)
        yield TestClient(main_mod.app)
    finally:
        if previous is None:
            os.environ.pop("FRONTEND_DIR", None)
        else:
            os.environ["FRONTEND_DIR"] = previous
        from app.config import get_settings
        get_settings.cache_clear()
        import app.main as main_mod
        importlib.reload(main_mod)


def test_root_serves_index_html(spa_client):
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "MHEAT SPA shell" in r.text


def test_assets_bundle_serves_raw_file(spa_client):
    r = spa_client.get("/assets/app.js")
    assert r.status_code == 200
    assert "fake bundled app" in r.text
    # Static mount sets a sensible content-type.
    assert r.headers["content-type"].startswith(("application/javascript", "text/javascript"))


def test_deep_link_falls_back_to_index_html(spa_client):
    """/some/spa/route must serve index.html so client-side routing works."""
    r = spa_client.get("/events/2022/07")
    assert r.status_code == 200
    assert "MHEAT SPA shell" in r.text


def test_static_non_asset_file_at_root_is_served(spa_client):
    r = spa_client.get("/robots.txt")
    assert r.status_code == 200
    assert "User-agent: *" in r.text


def test_unknown_api_path_is_404_not_spa(spa_client):
    """/api/missing must NEVER return the HTML shell — API clients rely on 404."""
    r = spa_client.get("/api/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    # Envelope shape from errors.py.
    assert body["error"]["status"] == 404


def test_api_paths_still_route_to_their_handlers(spa_client):
    r = spa_client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
