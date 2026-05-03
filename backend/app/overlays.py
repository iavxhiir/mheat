"""Sectoral overlays: aquaculture sites, Natura 2000 marine MPAs, seagrass.

Live WFS endpoints can be flaky, rate-limited, or unavailable from inside a
firewalled datalab. Each fetcher therefore falls back to a checked-in JSON
fixture under ``backend/app/fixtures/overlays/``. The fixtures are the
last-resort safety net only — every request first tries the live WFS and
caches a successful response on disk.

## Layer name tuning notes — verified against the live catalogues 2026-04

* **Aquaculture** — EMODnet Human Activities, GeoServer WFS at
  ``https://ows.emodnet-humanactivities.eu/wfs``.
  typeNames = ``emodnet:aquaculture`` (singular). Earlier builds used
  ``emodnet:aquaculture_points`` which the current GeoServer rejects with 400.
* **MPA** — EEA ArcGIS REST at
  ``.../ProtectedSites/Natura2000Sites/MapServer``. Layer 0 = Habitats
  Directive Sites (pSCI/SCI/SAC), 1 = Birds Directive (SPA), 2 = combined.
  We hit layer 2 with ``f=geojson&where=1=1&outSR=4326`` plus a bbox
  envelope. The legacy ``WFSServer`` at the same path returns 400.
* **Seagrass** — EMODnet Seabed Habitats GeoServer at
  ``https://ows.emodnet-seabedhabitats.eu/geoserver/emodnet_open/wfs``.
  typeNames = ``emodnet_open:seagrass_eov_poly_2025`` (Mediterranean
  Posidonia + Cymodocea polygons; the older ``emodnet:seagrass`` alias
  is gone, and the ``emodnet_open_maplibrary`` namespace only carries
  Article-17 reporting layers — no Posidonia coverage).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .cache import CacheStore
from .config import Settings

logger = logging.getLogger(__name__)

Overlay = dict[str, Any]  # GeoJSON FeatureCollection

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "overlays"
_FIXTURE_FILES: dict[str, str] = {
    "aquaculture": "aquaculture.json",
    "mpa": "mpa.json",
    "seagrass": "seagrass.json",
}

# Process-level memoization. Invalidated via :func:`clear_overlay_cache`
# (exposed for tests + admin routes).
_MEMO: dict[str, Overlay] = {}
_FIXTURE_READS: dict[str, int] = {}  # counter for tests / observability


def clear_overlay_cache() -> None:
    """Drop the in-process overlay memoization."""
    _MEMO.clear()
    _FIXTURE_READS.clear()


def _load_fixture(kind: str) -> Overlay:
    """Read the bundled fixture for the given overlay kind."""
    path = _FIXTURE_DIR / _FIXTURE_FILES[kind]
    _FIXTURE_READS[kind] = _FIXTURE_READS.get(kind, 0) + 1
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.error("Overlay fixture missing at %s", path)
        return {"type": "FeatureCollection", "features": []}


# ---------------------------------------------------------------------
@dataclass
class OverlayProvider:
    """Fetch sectoral overlays from EMODnet / EEA with bundled fallbacks."""

    settings: Settings
    cache: CacheStore
    timeout: float = 15.0

    # --- public ---
    def get(self, kind: str) -> Overlay:
        """Return a GeoJSON FeatureCollection for the requested overlay kind.

        Cached in-process after the first call so repeated impact-joins on a
        warm FastAPI worker become a dict lookup. Invalidate via
        :func:`clear_overlay_cache`.
        """
        kind = kind.lower().strip()
        if kind not in _FIXTURE_FILES:
            raise ValueError(f"Unknown overlay kind: {kind}")

        if kind in _MEMO:
            return _MEMO[kind]

        cache_key = f"overlay_{kind}"
        cached = self.cache.read_json(cache_key)
        if cached is not None:
            _MEMO[kind] = cached
            return cached

        try:
            data = self._fetch_live(kind)
            self.cache.write_json(cache_key, data)
            _MEMO[kind] = data
            return data
        except Exception as e:  # noqa: BLE001
            # Loud warning (not debug) so operators know the fallback is active.
            logger.warning(
                "Live %s WFS fetch FAILED: %s — falling back to bundled fixture at %s",
                kind, e, _FIXTURE_DIR / _FIXTURE_FILES[kind],
            )
            fallback = _load_fixture(kind)
            # Intentionally DO NOT memoize the fallback — we want the next
            # request to retry the live WFS and recover automatically.
            return fallback

    # --- internal ---
    def _fetch_live(self, kind: str) -> Overlay:
        """Hit the appropriate WFS / ArcGIS REST endpoint and return GeoJSON."""
        bbox = self.settings.bbox_tuple
        lon_min, lat_min, lon_max, lat_max = bbox

        if kind == "aquaculture":
            # `emodnet:aquaculture` is the regional grouping polygon (1 row);
            # `emodnet:finfish` is the layer of individual cage / pen sites
            # which is what we actually need for impact joins.
            url = self.settings.emodnet_aquaculture_wfs
            params: dict[str, str] = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "emodnet:finfish",
                "outputFormat": "application/json",
                "count": "2000",
                # WFS 2.0 bbox order is lat_min,lon_min,lat_max,lon_max,EPSG.
                "bbox": f"{lat_min},{lon_min},{lat_max},{lon_max},urn:ogc:def:crs:EPSG::4326",
                # Force response CRS to WGS84 / CRS84 — without this, the
                # GeoServer default may project to EPSG:3857 for web display
                # and OGC clients silently get reprojected coords.
                "srsName": "urn:ogc:def:crs:EPSG::4326",
            }
        elif kind == "mpa":
            # Natura 2000 is served via EEA's ArcGIS REST. Layer 2 = combined
            # Habitats + Birds Directive sites. The ArcGIS server 500s above
            # ~500 records or full-precision geometry at this bbox, so we
            # ask for round-to-4-decimals coords and a hard 500 cap which
            # comfortably covers the Mediterranean's ~hundreds of sites.
            base = self.settings.natura2000_wfs.rstrip("/")
            url = f"{base}/2/query"
            params = {
                "where": "1=1",
                # Real field names (per layer 2 schema):
                # SITECODE, SITENAME, MS (member state), SITETYPE,
                # RELEASE_DATE, Area_ha. Earlier passes used COUNTRY_CODE /
                # RELEASE_DA which the server rejects.
                "outFields": "SITECODE,SITENAME,MS,SITETYPE,RELEASE_DATE,Area_ha",
                "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "outSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "geometryPrecision": "4",
                "f": "geojson",
                "resultRecordCount": "500",
            }
        elif kind == "seagrass":
            url = self.settings.emodnet_seabed_wfs
            params = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "emodnet_open:seagrass_eov_poly_2025",
                "outputFormat": "application/json",
                "count": "2000",
                "bbox": f"{lat_min},{lon_min},{lat_max},{lon_max},urn:ogc:def:crs:EPSG::4326",
                # Force response CRS to WGS84; the seabed-habitats GeoServer
                # otherwise returns EPSG:3857 by default and OGC clients
                # display coords in Web Mercator metres instead of degrees.
                "srsName": "urn:ogc:def:crs:EPSG::4326",
            }
        else:
            raise ValueError(kind)

        logger.info("Overlay GET %s params=%s", url, params)
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, dict):
            raise ValueError("Upstream response is not a JSON object")
        # ArcGIS REST returns 200 with ``{"error": {"code": ..., "message": ...}}``
        # for invalid queries. Surface the real cause so operators don't see a
        # generic "not a FeatureCollection" message.
        if "error" in data and "type" not in data:
            err = data["error"] or {}
            raise ValueError(
                f"Upstream returned ArcGIS error {err.get('code', '?')}: "
                f"{err.get('message', 'unknown')}"
            )
        if data.get("type") != "FeatureCollection":
            raise ValueError("Upstream response is not a GeoJSON FeatureCollection")
        return data


def list_overlay_kinds() -> list[str]:
    """Return the list of supported overlay kinds."""
    return list(_FIXTURE_FILES.keys())
