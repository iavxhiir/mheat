"""Historical SST backfill.

Downloads the CMS reanalysis in yearly chunks from 1982 to present and
writes them into the permanent Zarr cube. Runtime is on the order of hours
and the final cube is ~10 GB for the Mediterranean at 0.05° resolution, so
this is a one-off bootstrap job.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

sys.path.insert(0, "/srv/app")

from app.cache import CacheStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.sst import SSTProvider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser(description="MHEAT historical backfill")
    parser.add_argument("--start-year", type=int, default=1982)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    args = parser.parse_args()

    settings = get_settings()
    if not settings.credentials_present():
        log.error("CMS credentials missing.")
        return 2

    cache = CacheStore(settings.cache_dir, settings.zarr_store)
    sst = SSTProvider(settings=settings, cache=cache)

    for y in range(args.start_year, args.end_year + 1):
        log.info("Backfilling year %d", y)
        start = date(y, 1, 1)
        end = date(y, 12, 31)
        try:
            sst.load_range(start, end)
        except Exception as e:  # noqa: BLE001
            log.error("Year %d failed: %s", y, e)
            continue
        log.info("Year %d done", y)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
