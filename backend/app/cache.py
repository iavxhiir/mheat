"""Filesystem cache and Zarr store abstraction.

We keep a single Zarr store per deployment containing the gridded SST cube
(and derived MHW diagnostics) for the study area. Ancillary JSON caches for
overlays live next to it in ``cache_dir``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CacheStore:
    """Thin wrapper around a cache directory plus a Zarr path."""

    cache_dir: Path
    zarr_path: Path

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.zarr_path = Path(self.zarr_path)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- JSON helpers ------------------------------------------------
    def json_path(self, key: str) -> Path:
        """Resolve a namespaced JSON cache file path."""
        safe = key.replace("/", "_").replace("..", "_")
        return self.cache_dir / f"{safe}.json"

    def read_json(self, key: str) -> Any | None:
        """Return cached JSON by key, or None if absent."""
        p = self.json_path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write_json(self, key: str, data: Any) -> Path:
        """Persist JSON data under key, returning the file path."""
        p = self.json_path(key)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    # ---- Zarr helpers ------------------------------------------------
    def zarr_exists(self) -> bool:
        """True if the Zarr store on disk has at least a .zgroup or .zarray."""
        if not self.zarr_path.exists():
            return False
        return any(self.zarr_path.rglob(".zgroup")) or any(self.zarr_path.rglob(".zarray"))
