"""Build a STAC 1.0.0 Collection + yearly Items for the MHEAT-MED catalogue.

When run with ``--dry-run`` (default), writes a self-contained STAC tree to
``--out`` and validates each Object with ``pystac.validate()`` so CI and
reviewers can confirm STAC correctness without any EDITO network access.

When run without ``--dry-run``, POSTs the Collection + Items to the EDITO
STAC API (requires ``EDITO_STAC_URL`` + ``EDITO_STAC_TOKEN`` env vars).

The ID / spatial extent / temporal extent / provider / licence fields
match the MHEAT proposal §1.10 dataset description.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pystac
from pystac import CatalogType, Collection, Extent, Item, Provider, ProviderRole, SpatialExtent, TemporalExtent

ROOT = Path(__file__).resolve().parents[1]

COLLECTION_ID = "mhw-events"
TITLE = "MHEAT-MED — Mediterranean Marine Heatwave event catalogue"
DESCRIPTION = (
    "Derived Marine Heatwave events detected in the Mediterranean and "
    "Adriatic seas, computed per Hobday et al. (2016) on Copernicus Marine "
    "SST products. Variables: sst, climatology, threshold_90p, anomaly, "
    "mhw_flag, mhw_category (Hobday 2018, 0-5). ARCO Zarr + COG overviews. "
    "CC-BY-4.0."
)

MED_BBOX = [-6.0, 30.0, 37.0, 46.0]
TEMPORAL_START = datetime(1982, 1, 1, tzinfo=timezone.utc)


def _build_collection(temporal_end: datetime) -> Collection:
    providers = [
        Provider(
            name="MHEAT",
            roles=[ProviderRole.PRODUCER, ProviderRole.PROCESSOR, ProviderRole.HOST],
            url="https://github.com/your-org/mheat",
        ),
        Provider(
            name="Copernicus Marine Service",
            roles=[ProviderRole.LICENSOR, ProviderRole.PRODUCER],
            url="https://marine.copernicus.eu",
        ),
        Provider(
            name="EMODnet",
            roles=[ProviderRole.LICENSOR],
            url="https://emodnet.ec.europa.eu",
        ),
    ]
    extent = Extent(
        spatial=SpatialExtent([MED_BBOX]),
        temporal=TemporalExtent([[TEMPORAL_START, temporal_end]]),
    )
    collection = Collection(
        id=COLLECTION_ID,
        title=TITLE,
        description=DESCRIPTION,
        license="CC-BY-4.0",
        extent=extent,
        providers=providers,
        keywords=[
            "marine heatwave", "Hobday 2016", "Mediterranean", "Adriatic",
            "Copernicus Marine", "EDITO", "MSFD Descriptor 7",
            "aquaculture", "MPA", "seagrass",
        ],
        summaries=pystac.Summaries({
            "variables": ["sst", "climatology", "threshold_90p", "anomaly",
                          "mhw_flag", "mhw_category"],
            "mhw_category": {"minimum": 0, "maximum": 5},
        }),
    )
    collection.stac_extensions = [
        "https://stac-extensions.github.io/scientific/v1.0.0/schema.json",
    ]
    collection.extra_fields["sci:doi"] = "10.5281/zenodo.mheat-latest"
    collection.extra_fields["sci:citation"] = (
        "MHEAT contributors (2027). MHEAT-MED Marine Heatwave Event Catalogue. "
        "Derived from Copernicus Marine SST using Hobday et al. (2016)."
    )
    collection.extra_fields["sci:publications"] = [
        {
            "doi": "10.1016/j.pocean.2015.12.014",
            "citation": "Hobday A.J. et al. (2016) A hierarchical approach to defining marine heatwaves. Progress in Oceanography 141, 227-238.",
        },
        {
            "doi": "10.5670/oceanog.2018.205",
            "citation": "Hobday A.J. et al. (2018) Categorizing and Naming Marine Heatwaves. Oceanography 31(2), 162-173.",
        },
    ]
    return collection


def _build_yearly_item(year: int) -> Item:
    t0 = datetime(year, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [MED_BBOX[0], MED_BBOX[1]],
            [MED_BBOX[2], MED_BBOX[1]],
            [MED_BBOX[2], MED_BBOX[3]],
            [MED_BBOX[0], MED_BBOX[3]],
            [MED_BBOX[0], MED_BBOX[1]],
        ]],
    }
    item = Item(
        id=f"mhw-events-{year}",
        geometry=geometry,
        bbox=MED_BBOX,
        datetime=None,
        start_datetime=t0,
        end_datetime=t1,
        properties={
            "mheat:year": year,
            "mheat:clim_start": 1991,
            "mheat:clim_end": 2020,
            "mheat:threshold_percentile": 90,
            "mheat:min_duration_days": 5,
            "mheat:gap_join_days": 2,
        },
    )
    item.add_asset(
        "zarr",
        pystac.Asset(
            href=f"s3://edito-mheat/mhw-events/{year}.zarr",
            media_type="application/vnd+zarr",
            title=f"{year} MHW ARCO Zarr cube",
            roles=["data"],
        ),
    )
    item.add_asset(
        "cog-preview",
        pystac.Asset(
            href=f"s3://edito-mheat/mhw-events/{year}_preview.tif",
            media_type="image/tiff; application=geotiff; profile=cloud-optimized",
            title=f"{year} MHW category COG overview (10 km)",
            roles=["overview"],
        ),
    )
    return item


def build_tree(out: Path, years: list[int]) -> Collection:
    collection = _build_collection(
        temporal_end=datetime(max(years), 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    for y in years:
        collection.add_item(_build_yearly_item(y))

    out.mkdir(parents=True, exist_ok=True)
    collection.normalize_hrefs(str(out))
    collection.save(catalog_type=CatalogType.SELF_CONTAINED)
    return collection


def validate_tree(out: Path) -> int:
    collection = Collection.from_file(str(out / "collection.json"))
    collection.validate()
    n = 0
    for item in collection.get_items(recursive=True):
        item.validate()
        n += 1
    print(f"STAC tree validates — 1 Collection + {n} Items at {out}")
    return n


def post_to_edito(out: Path) -> None:
    """POST the Collection + Items to the EDITO STAC API.

    Requires environment variables ``EDITO_STAC_URL`` (base URL, without
    trailing slash) and ``EDITO_STAC_TOKEN`` (OIDC bearer token).
    """
    import httpx  # noqa: WPS433 — local import to keep --dry-run cheap

    base = os.environ.get("EDITO_STAC_URL", "").rstrip("/")
    token = os.environ.get("EDITO_STAC_TOKEN", "")
    if not base or not token:
        raise SystemExit("EDITO_STAC_URL and EDITO_STAC_TOKEN must be set for live registration")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        col = json.loads((out / "collection.json").read_text(encoding="utf-8"))
        r = client.put(f"{base}/collections/{col['id']}", json=col)
        r.raise_for_status()
        for item_path in out.rglob("*.json"):
            if item_path.name == "collection.json":
                continue
            item = json.loads(item_path.read_text(encoding="utf-8"))
            if item.get("type") != "Feature":
                continue
            r = client.put(f"{base}/collections/{col['id']}/items/{item['id']}", json=item)
            r.raise_for_status()
    print("Registered Collection + Items on EDITO STAC")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "stac",
                        help="Local STAC tree directory.")
    parser.add_argument("--years", type=int, nargs="+",
                        default=list(range(2020, 2028)),
                        help="Yearly STAC Items to generate.")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="(Default) Write + validate locally, do not POST to EDITO.")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        help="POST the tree to EDITO STAC (requires env vars).")
    args = parser.parse_args(argv)

    build_tree(args.out, args.years)
    validate_tree(args.out)

    if not args.dry_run:
        post_to_edito(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
