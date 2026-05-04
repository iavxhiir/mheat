"""Dynamic STAC catalog for the MHEAT derived dataset.

Items are derived from whatever data the service currently has access to:

* **Cache present**: scan the on-disk SST Zarr for its time axis; one Item
  per calendar year that has data.
* **Fallback**: a hardcoded pair of Items covering 2022 and 2024 so the
  endpoint is never empty on a cold container.
* **Climatology**: when the pre-computed Hobday baseline zarr is present,
  it is surfaced as a first-class Item so reviewers see it as a discoverable,
  citable data product. See :func:`build_climatology_item`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .cache import CacheStore
from .config import Settings

if TYPE_CHECKING:
    from .climatology import Climatology

logger = logging.getLogger(__name__)


COLLECTION_ID = "mheat-med-mhw"
CLIMATOLOGY_ZARR_HREF = "/api/data/climatology.zarr"
SST_ZARR_HREF = "/api/data/sst.zarr"


def build_collection(time_interval: list[list[str | None]] | None = None,
                     bbox: list[list[float]] | None = None) -> dict[str, Any]:
    """Return the MHEAT STAC Collection document."""
    extent_spatial = bbox or [[-6.0, 30.0, 36.5, 46.0]]
    extent_temporal = time_interval or [["1982-01-01T00:00:00Z", None]]
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": COLLECTION_ID,
        "title": "MHEAT — Mediterranean Marine Heatwaves (derived)",
        "description": (
            "Derived gridded marine heatwave diagnostics for the Mediterranean "
            "and Adriatic, computed using the Hobday et al. (2016) method on "
            "Copernicus Marine SST products."
        ),
        "license": "CC-BY-4.0",
        "extent": {
            "spatial": {"bbox": extent_spatial},
            "temporal": {"interval": extent_temporal},
        },
        "providers": [
            {"name": "MHEAT project", "roles": ["producer", "processor"]},
            {
                "name": "Copernicus Marine Service",
                "roles": ["licensor"],
                "url": "https://data.marine.copernicus.eu",
            },
        ],
        "summaries": {
            "mhw:categories": ["I Moderate", "II Strong", "III Severe", "IV Extreme", "V Super-Extreme"],
            "mhw:method": ["Hobday2016"],
        },
        "links": [
            {"rel": "self", "href": f"/api/stac/collections/{COLLECTION_ID}", "type": "application/json"},
            {"rel": "items", "href": f"/api/stac/collections/{COLLECTION_ID}/items", "type": "application/json"},
            {"rel": "parent", "href": "/api/stac/collections", "type": "application/json"},
        ],
    }


def _scan_store(settings: Settings, cache: CacheStore) -> tuple[list[tuple[int, str, str]], list[float]] | None:
    """Peek at the SST cube and return (year_ranges, bbox).

    year_ranges is a list of ``(year, start_iso, end_iso)`` tuples, one per
    calendar year that has data; bbox is ``[lon_min, lat_min, lon_max, lat_max]``.
    Returns None if nothing is inspectable.
    """
    try:
        from .sst import SSTProvider
        provider = SSTProvider(settings=settings, cache=cache)
        ds = provider.load()
    except Exception as exc:  # noqa: BLE001
        logger.info("STAC scan: cannot load cube (%s), falling back to static", exc)
        return None

    try:
        times = ds["time"].values
        if times.size == 0:
            return None
        years = sorted({int(str(t)[:4]) for t in times})
        year_ranges: list[tuple[int, str, str]] = []
        for y in years:
            year_times = [str(t)[:10] for t in times if str(t).startswith(str(y))]
            if year_times:
                year_ranges.append((y, year_times[0], year_times[-1]))
        lat_name = "latitude" if "latitude" in ds.coords else "lat"
        lon_name = "longitude" if "longitude" in ds.coords else "lon"
        lons = ds[lon_name].values
        lats = ds[lat_name].values
        bbox = [float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())]
        return year_ranges, bbox
    except Exception as exc:  # noqa: BLE001
        logger.warning("STAC scan: inspection failed (%s)", exc)
        return None


def build_items(settings: Settings | None = None,
                cache: CacheStore | None = None,
                climatology: Climatology | None = None) -> list[dict[str, Any]]:
    """Build STAC Items dynamically from whatever data is available.

    Args:
        settings: Service settings; needed for the dynamic-scan path.
        cache: Cache store; needed for the dynamic-scan path.
        climatology: When non-None, an additional item describing the
            pre-computed Hobday baseline is appended to the list.
    """
    scan = None
    if settings is not None and cache is not None:
        scan = _scan_store(settings, cache)

    if scan is None:
        # Hardcoded fallback for offline tests / empty stores.
        items = _fallback_items()
    else:
        year_ranges, bbox = scan
        items = []
        for y, d0, d1 in year_ranges:
            items.append({
                "type": "Feature",
                "stac_version": "1.0.0",
                "id": f"mheat-med-{y}",
                "collection": COLLECTION_ID,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [bbox[0], bbox[1]], [bbox[2], bbox[1]],
                        [bbox[2], bbox[3]], [bbox[0], bbox[3]],
                        [bbox[0], bbox[1]],
                    ]],
                },
                "bbox": bbox,
                "properties": {
                    "datetime": None,
                    "start_datetime": f"{d0}T00:00:00Z",
                    "end_datetime": f"{d1}T23:59:59Z",
                    "mhw:method": "Hobday2016",
                    "mhw:climatology": f"{settings.clim_start}-{settings.clim_end}" if settings else "1991-2020",
                    "title": f"{y} Mediterranean MHW season",
                    "indexed:source": "zarr" if (cache is not None and cache.zarr_exists()) else "fallback",
                },
                "assets": {
                    "events": {
                        "href": f"/api/events?start={d0}&end={d1}",
                        "type": "application/geo+json",
                        "roles": ["data"],
                        "title": f"Detected MHW events {y} (GeoJSON)",
                    },
                    "anomaly": {
                        "href": f"/api/anomaly?date={d0}",
                        "type": "image/png",
                        "roles": ["visual"],
                        "title": f"SST anomaly {d0}",
                    },
                },
                "links": [
                    {"rel": "self", "href": f"/api/stac/collections/{COLLECTION_ID}/items/mheat-med-{y}"},
                    {"rel": "parent", "href": f"/api/stac/collections/{COLLECTION_ID}"},
                    {"rel": "collection", "href": f"/api/stac/collections/{COLLECTION_ID}"},
                ],
            })

    # Surface the cached SST cube itself as a first-class ARCO asset so STAC
    # clients can discover the input dataset, not just the derived MHW events.
    # EDITO §5 mandates ARCO/Zarr for dataset outputs and the cube qualifies.
    if settings is not None and cache is not None and cache.zarr_exists():
        try:
            items.append(build_sst_cube_item(settings, cache))
        except Exception as exc:  # noqa: BLE001
            logger.warning("STAC: skipping sst.zarr item: %s", exc)

    if climatology is not None:
        items.append(build_climatology_item(climatology))
    return items


def build_sst_cube_item(settings: Settings, cache: CacheStore) -> dict[str, Any]:
    """Return a STAC 1.0 Item describing the cached SST Zarr cube.

    EDITO §5 requires dataset outputs to be ARCO. The SST cube populated by
    the startup prefetch and lazy CMS fill *is* the canonical ARCO asset;
    this item makes it discoverable from the STAC catalog so reviewers and
    downstream pipelines can stream chunks via :mod:`xarray.open_zarr`
    without going through any of the JSON / CSV / PNG endpoints.
    """
    from .sst import SSTProvider

    provider = SSTProvider(settings=settings, cache=cache)
    extent = provider.cube_extent()
    if extent is None:
        raise ValueError("SST cube exists on disk but has no time axis")
    start_date, end_date = extent

    ds = provider.cube()
    assert ds is not None  # noqa: S101 — extent check above implies presence

    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    lons = ds[lon_name].values
    lats = ds[lat_name].values
    bbox = [
        float(lons.min()), float(lats.min()),
        float(lons.max()), float(lats.max()),
    ]
    lon_min, lat_min, lon_max, lat_max = bbox

    item_id = f"mheat-sst-cube-mediterranean-{start_date.year}-{end_date.year}"
    cube_size = sum(
        f.stat().st_size for f in cache.zarr_path.rglob("*") if f.is_file()
    )
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": COLLECTION_ID,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [lon_min, lat_min], [lon_max, lat_min],
                [lon_max, lat_max], [lon_min, lat_max],
                [lon_min, lat_min],
            ]],
        },
        "bbox": bbox,
        "properties": {
            "datetime": None,
            "start_datetime": f"{start_date.isoformat()}T00:00:00Z",
            "end_datetime": f"{end_date.isoformat()}T23:59:59Z",
            "title": "Mediterranean Analysed SST cube (cached, ARCO Zarr)",
            "description": (
                "Cached Copernicus Marine SST cube for the Mediterranean & "
                "Adriatic, persisted as a chunked ARCO Zarr v2 store with "
                "consolidated metadata. Populated incrementally by the "
                "MHEAT startup prefetch (last 90 days of NRT) and the "
                "request-path lazy fill against the CMS NRT, reanalysis "
                "and analysis-and-forecast products."
            ),
            "mhw:source_dataset": "Copernicus Marine MED SST L4 NRT / "
                                  "Reanalysis / Analysis-Forecast",
            "mhw:cache_size_bytes": int(cube_size),
            "providers": [
                {"name": "MHEAT project", "roles": ["processor"]},
                {
                    "name": "Copernicus Marine Service",
                    "roles": ["producer", "licensor"],
                    "url": "https://data.marine.copernicus.eu",
                },
            ],
        },
        "assets": {
            "zarr": {
                "href": SST_ZARR_HREF,
                "type": "application/vnd+zarr",
                "roles": ["data"],
                "title": "SST cube (Zarr)",
                "description": (
                    "Open with `xarray.open_zarr(<base>/api/data/sst.zarr, "
                    "consolidated=True)`."
                ),
            },
            "documentation": {
                "href": "/api/docs",
                "type": "text/html",
                "roles": ["metadata"],
                "title": "MHEAT API documentation",
            },
        },
        "links": [
            {"rel": "self",
             "href": f"/api/stac/collections/{COLLECTION_ID}/items/{item_id}",
             "type": "application/json"},
            {"rel": "parent",
             "href": f"/api/stac/collections/{COLLECTION_ID}",
             "type": "application/json"},
            {"rel": "collection",
             "href": f"/api/stac/collections/{COLLECTION_ID}",
             "type": "application/json"},
        ],
    }


def build_climatology_item(
    climatology: Climatology, base_url: str = "/api"
) -> dict[str, Any]:
    """Return a STAC 1.0.0 Item describing the pre-computed Hobday baseline.

    Surfaces the on-disk ``climatology.zarr`` as a discoverable, citable data
    product so EDITO reviewers can cite it directly from the catalog.

    Provenance fields are read from ``climatology.attrs`` (populated by
    :func:`app.climatology.build_climatology_from_cube`):

    * ``clim_start`` / ``clim_end`` — reference period years.
    * ``bbox`` — ``[lon_min, lat_min, lon_max, lat_max]``; falls back to the
      Mediterranean envelope when the artifact predates that attr.
    * ``source_dataset``, ``grid_resolution``, ``created_utc``.
    * ``pctile``, ``window_half_width``, ``smooth_width`` — Hobday knobs.

    Args:
        climatology: The opened climatology artifact.
        base_url: API surface prefix used to construct asset hrefs. Defaults
            to ``/api`` to match the relative-path pattern used by other STAC
            items in this catalog.

    Returns:
        A STAC Item (GeoJSON Feature with STAC properties) ready for
        inclusion in an ItemCollection.
    """
    attrs = climatology.attrs or {}
    clim_start = int(attrs.get("clim_start", 1991))
    clim_end = int(attrs.get("clim_end", 2020))

    bbox_attr = attrs.get("bbox")
    if isinstance(bbox_attr, (list, tuple)) and len(bbox_attr) == 4:
        bbox = [float(b) for b in bbox_attr]
    else:
        # Fallback: derive from the artifact's own coordinates.
        try:
            lats = climatology.seas["latitude"].values
            lons = climatology.seas["longitude"].values
            bbox = [
                float(lons.min()), float(lats.min()),
                float(lons.max()), float(lats.max()),
            ]
        except Exception:  # noqa: BLE001
            bbox = [-6.0, 30.0, 36.5, 46.0]

    lon_min, lat_min, lon_max, lat_max = bbox
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min], [lon_max, lat_min],
            [lon_max, lat_max], [lon_min, lat_max],
            [lon_min, lat_min],
        ]],
    }

    source_dataset = str(attrs.get("source_dataset", "")) or "Copernicus Marine SST"
    grid_resolution = str(attrs.get("grid_resolution", "")) or "native"
    pctile = float(attrs.get("pctile", 90.0))
    window_half_width = int(attrs.get("window_half_width", 5))
    smooth_width = int(attrs.get("smooth_width", 31))
    created_utc = attrs.get("created_utc")

    item_id = f"mhw-climatology-mediterranean-{clim_start}-{clim_end}"
    description = (
        f"Per-DOY pre-computed Hobday baseline derived from {source_dataset}. "
        f"Contains the {smooth_width}-day smoothed seasonal mean (`seas`) and "
        f"{int(pctile)}th-percentile threshold (`thresh`) on a regular "
        f"latitude/longitude grid (366 days of year × lat × lon). "
        f"Built with a ±{window_half_width}-day DOY pool over the "
        f"{clim_start}-{clim_end} reference period, following Hobday et al. "
        "(2016). Eliminates the 30-year recomputation otherwise needed for "
        "every marine-heatwave detection request."
    )

    properties: dict[str, Any] = {
        "datetime": None,
        "start_datetime": f"{clim_start}-01-01T00:00:00Z",
        "end_datetime": f"{clim_end}-12-31T23:59:59Z",
        "title": (
            "Mediterranean SST Climatology "
            "(Hobday seasonal mean + 90th-percentile threshold)"
        ),
        "description": description,
        "mhw:method": "Hobday2016",
        "mhw:climatology": f"{clim_start}-{clim_end}",
        "mhw:percentile": pctile,
        "mhw:window_half_width": window_half_width,
        "mhw:smooth_width": smooth_width,
        "mhw:source_dataset": source_dataset,
        "mhw:grid_resolution": grid_resolution,
        "providers": [
            {
                "name": "MHEAT project",
                "roles": ["producer", "processor"],
            },
            {
                "name": "Copernicus Marine Service",
                "roles": ["licensor"],
                "url": "https://data.marine.copernicus.eu",
            },
        ],
    }
    if created_utc:
        properties["created"] = str(created_utc)

    assets = {
        "zarr": {
            "href": CLIMATOLOGY_ZARR_HREF,
            "type": "application/vnd+zarr",
            "roles": ["data"],
            "title": "Climatology zarr",
            "description": (
                "Hobday seasonal mean and percentile threshold, persisted as "
                "a chunked, consolidated zarr v2 store."
            ),
        },
        "documentation": {
            "href": f"{base_url}/docs",
            "type": "text/html",
            "roles": ["metadata"],
            "title": "MHEAT API documentation",
        },
    }

    links = [
        {
            "rel": "self",
            "href": f"{base_url}/stac/collections/{COLLECTION_ID}/items/{item_id}",
            "type": "application/json",
        },
        {
            "rel": "parent",
            "href": f"{base_url}/stac/collections/{COLLECTION_ID}",
            "type": "application/json",
        },
        {
            "rel": "collection",
            "href": f"{base_url}/stac/collections/{COLLECTION_ID}",
            "type": "application/json",
        },
    ]

    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": COLLECTION_ID,
        "geometry": geometry,
        "bbox": bbox,
        "properties": properties,
        "assets": assets,
        "links": links,
    }


def _fallback_items() -> list[dict[str, Any]]:
    """Static Items used when no data is inspectable."""
    return [
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "mheat-med-2022",
            "collection": COLLECTION_ID,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-6.0, 30.0], [36.5, 30.0], [36.5, 46.0], [-6.0, 46.0], [-6.0, 30.0]]],
            },
            "bbox": [-6.0, 30.0, 36.5, 46.0],
            "properties": {
                "datetime": None,
                "start_datetime": "2022-05-01T00:00:00Z",
                "end_datetime": "2022-12-31T23:59:59Z",
                "mhw:method": "Hobday2016",
                "mhw:climatology": "1991-2020",
                "title": "2022 Mediterranean MHW season",
                "indexed:source": "fallback",
            },
            "assets": {
                "events": {
                    "href": "/api/events?start=2022-05-01&end=2022-12-31",
                    "type": "application/geo+json",
                    "roles": ["data"],
                    "title": "Detected MHW events (GeoJSON)",
                },
            },
            "links": [
                {"rel": "self", "href": f"/api/stac/collections/{COLLECTION_ID}/items/mheat-med-2022"},
                {"rel": "parent", "href": f"/api/stac/collections/{COLLECTION_ID}"},
                {"rel": "collection", "href": f"/api/stac/collections/{COLLECTION_ID}"},
            ],
        },
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "mheat-med-2024",
            "collection": COLLECTION_ID,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-6.0, 30.0], [36.5, 30.0], [36.5, 46.0], [-6.0, 46.0], [-6.0, 30.0]]],
            },
            "bbox": [-6.0, 30.0, 36.5, 46.0],
            "properties": {
                "datetime": None,
                "start_datetime": "2024-05-01T00:00:00Z",
                "end_datetime": "2024-12-31T23:59:59Z",
                "mhw:method": "Hobday2016",
                "mhw:climatology": "1991-2020",
                "title": "2024 Mediterranean MHW season",
                "indexed:source": "fallback",
            },
            "assets": {
                "events": {
                    "href": "/api/events?start=2024-05-01&end=2024-12-31",
                    "type": "application/geo+json",
                    "roles": ["data"],
                    "title": "Detected MHW events (GeoJSON)",
                },
            },
            "links": [
                {"rel": "self", "href": f"/api/stac/collections/{COLLECTION_ID}/items/mheat-med-2024"},
                {"rel": "parent", "href": f"/api/stac/collections/{COLLECTION_ID}"},
                {"rel": "collection", "href": f"/api/stac/collections/{COLLECTION_ID}"},
            ],
        },
    ]
