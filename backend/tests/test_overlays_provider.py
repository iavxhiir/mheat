"""Tests for `app.overlays.OverlayProvider`."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.cache import CacheStore
from app.config import Settings
from app.overlays import OverlayProvider, list_overlay_kinds


@pytest.fixture()
def demo_provider(tmp_path) -> OverlayProvider:
    settings = Settings(demo_mode=True, cache_dir=tmp_path, zarr_store=tmp_path / "sst.zarr")
    return OverlayProvider(
        settings=settings,
        cache=CacheStore(cache_dir=tmp_path, zarr_path=tmp_path / "sst.zarr"),
    )


@pytest.fixture()
def live_provider(tmp_path) -> OverlayProvider:
    settings = Settings(demo_mode=False, cache_dir=tmp_path, zarr_store=tmp_path / "sst.zarr")
    return OverlayProvider(
        settings=settings,
        cache=CacheStore(cache_dir=tmp_path, zarr_path=tmp_path / "sst.zarr"),
    )


def test_list_overlay_kinds_returns_the_three_known_kinds():
    assert set(list_overlay_kinds()) == {"aquaculture", "mpa", "seagrass"}


@pytest.mark.parametrize("kind", ["aquaculture", "mpa", "seagrass"])
def test_demo_mode_returns_bundled_fixture(demo_provider, kind: str):
    data = demo_provider.get(kind)
    assert data["type"] == "FeatureCollection"
    assert isinstance(data["features"], list)


def test_unknown_kind_raises(demo_provider):
    with pytest.raises(ValueError, match="Unknown overlay kind"):
        demo_provider.get("coral_reefs")


def test_live_mode_falls_back_to_fixture_on_http_error(live_provider, caplog):
    """When the WFS blows up, we log a WARNING and return the bundled fixture."""
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.side_effect = (
            httpx.ConnectError("no route to host")
        )
        data = live_provider.get("mpa")
    # A real fallback returns valid GeoJSON from the fixture on disk.
    assert data["type"] == "FeatureCollection"
    # And the operator is loudly warned so the fallback can't hide silently.
    assert any(
        "Live mpa WFS fetch FAILED" in rec.message for rec in caplog.records
        if rec.levelname == "WARNING"
    )


def test_live_mode_rejects_non_featurecollection_response(live_provider):
    """Non-GeoJSON WFS responses trigger the fallback, not a corrupt cache."""
    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"error": "schema mismatch"}

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = _FakeResp()
        data = live_provider.get("aquaculture")
    # Fallback produces valid GeoJSON, not the garbage body.
    assert data["type"] == "FeatureCollection"
    assert "error" not in data


def test_live_mode_caches_successful_fetch(live_provider, tmp_path):
    """A successful fetch is written to the CacheStore and re-used on the next call."""
    sample = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": None}],
    }

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return sample

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = _FakeResp()
        first = live_provider.get("seagrass")
        # Second call must be cached — reset the mock to prove the cache is used.
        mock_client.return_value.__enter__.return_value.get.reset_mock()
        second = live_provider.get("seagrass")
        assert mock_client.return_value.__enter__.return_value.get.call_count == 0
    assert first == sample
    assert second == sample
