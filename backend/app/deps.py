"""FastAPI dependency-injection helpers."""

from __future__ import annotations

from fastapi import Depends

from .cache import CacheStore
from .config import Settings, get_settings
from .sst import SSTProvider


def settings_dep() -> Settings:
    """Dependency returning the Settings singleton."""
    return get_settings()


def cache_dep(settings: Settings = Depends(settings_dep)) -> CacheStore:
    """Dependency returning a filesystem-backed cache store."""
    return CacheStore(settings.cache_dir, settings.zarr_store)


def sst_dep(
    settings: Settings = Depends(settings_dep),
    cache: CacheStore = Depends(cache_dep),
) -> SSTProvider:
    """Dependency returning an SST data provider (demo or real CMS)."""
    return SSTProvider(settings=settings, cache=cache)
