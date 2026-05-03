"""Tests for the process-level OverlayProvider memoization."""

from __future__ import annotations

import unittest.mock as mock
from typing import Any

import pytest

from app.cache import CacheStore
from app.config import Settings
from app.overlays import OverlayProvider, _FIXTURE_READS, _MEMO, clear_overlay_cache


@pytest.fixture(autouse=True)
def _clean_overlay_cache():
    clear_overlay_cache()
    yield
    clear_overlay_cache()


def _provider(tmp_path) -> OverlayProvider:
    settings = Settings(
        cache_dir=tmp_path, zarr_store=tmp_path / "sst.zarr",
    )
    return OverlayProvider(
        settings=settings,
        cache=CacheStore(cache_dir=tmp_path, zarr_path=tmp_path / "sst.zarr"),
    )


def _patched_live_fetch(monkeypatch, *, n_features: int = 3, fail: bool = False):
    """Patch ``OverlayProvider._fetch_live`` to return a tiny GeoJSON or fail."""
    if fail:
        def fake_fetch(self, kind: str) -> Any:
            raise RuntimeError("simulated WFS outage")
    else:
        def fake_fetch(self, kind: str) -> Any:
            return {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": None, "properties": {"i": i, "k": kind}}
                    for i in range(n_features)
                ],
            }
    monkeypatch.setattr(OverlayProvider, "_fetch_live", fake_fetch)


def test_subsequent_calls_hit_the_in_process_memo(tmp_path, monkeypatch):
    """After the first successful fetch, repeat calls must skip the network."""
    n = {"calls": 0}
    def fake_fetch(self, kind: str):
        n["calls"] += 1
        return {"type": "FeatureCollection", "features": [{"type": "Feature",
                                                           "geometry": None,
                                                           "properties": {}}]}
    monkeypatch.setattr(OverlayProvider, "_fetch_live", fake_fetch)
    p = _provider(tmp_path)
    p.get("mpa")
    p.get("mpa")
    p.get("mpa")
    assert n["calls"] == 1, "memo must serve the second + third calls"


def test_different_kinds_are_cached_independently(tmp_path, monkeypatch):
    """Each overlay kind must be fetched and memoized in isolation."""
    seen: list[str] = []
    def fake_fetch(self, kind: str) -> Any:
        seen.append(kind)
        return {"type": "FeatureCollection", "features": []}
    monkeypatch.setattr(OverlayProvider, "_fetch_live", fake_fetch)
    p = _provider(tmp_path)
    p.get("mpa"); p.get("aquaculture"); p.get("seagrass")
    p.get("mpa"); p.get("aquaculture"); p.get("seagrass")
    # Each kind fetched exactly once even though we asked twice.
    assert sorted(seen) == ["aquaculture", "mpa", "seagrass"]


def test_clear_overlay_cache_drops_the_memo(tmp_path, monkeypatch):
    """clear_overlay_cache wipes the in-process memo (not the on-disk JSON cache).

    After clearing the memo, the next ``get`` reads from the on-disk JSON
    cache that the first ``get`` populated — so ``_fetch_live`` is NOT
    called a second time. The contract under test is "memo doesn't leak
    across clears", which we verify by confirming the kind is no longer
    in ``_MEMO`` immediately after the clear.
    """
    n_calls = {"x": 0}
    def fake_fetch(self, kind: str) -> Any:
        n_calls["x"] += 1
        return {"type": "FeatureCollection", "features": []}
    monkeypatch.setattr(OverlayProvider, "_fetch_live", fake_fetch)
    p = _provider(tmp_path)
    p.get("mpa")
    assert n_calls["x"] == 1
    assert "mpa" in _MEMO, "first call must populate the memo"
    clear_overlay_cache()
    assert "mpa" not in _MEMO, "clear_overlay_cache must drop the memo entry"
    # Subsequent get reads from the disk-cached JSON without re-fetching;
    # the memo is repopulated from disk on that read.
    p.get("mpa")
    assert "mpa" in _MEMO


def test_live_fetch_failure_falls_back_to_bundled_fixture(tmp_path, monkeypatch):
    """When the live fetch raises, the bundled JSON fixture is returned."""
    _patched_live_fetch(monkeypatch, fail=True)
    p = _provider(tmp_path)
    data = p.get("mpa")
    assert data["type"] == "FeatureCollection"
    # The fixture-read counter incremented because we fell back.
    assert _FIXTURE_READS.get("mpa", 0) >= 1


def test_fallback_is_not_memoized_so_next_call_retries_live(tmp_path, monkeypatch):
    """A live failure must not poison the memo — the next request re-attempts."""
    n = {"calls": 0}
    def flaky(self, kind: str) -> Any:
        n["calls"] += 1
        raise RuntimeError("simulated WFS outage")
    monkeypatch.setattr(OverlayProvider, "_fetch_live", flaky)
    p = _provider(tmp_path)
    p.get("mpa")
    p.get("mpa")
    p.get("mpa")
    assert n["calls"] == 3, "every call must re-attempt the live fetch"
    # And the kind is NOT in the in-memory memo, so a future provider sees it fresh.
    assert "mpa" not in _MEMO
