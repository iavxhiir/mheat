"""Daily update job.

1. Pulls yesterday's SST slice from the CMS NRT product.
2. Appends it to the persistent Zarr store.
3. Re-runs MHW detection for the trailing 60 days and refreshes the cached
   events GeoJSON.

Intended to be invoked by cron / a k8s CronJob. Exits non-zero on failure.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta

import xarray as xr

sys.path.insert(0, "/srv/app")  # container layout

from app.cache import CacheStore  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.mhw import detect_cube, events_to_geojson  # noqa: E402
from app.sst import SSTProvider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("update_daily")


def main() -> int:
    settings = get_settings()
    if not settings.credentials_present():
        log.error("CMS credentials missing. Set COPERNICUSMARINE_SERVICE_USERNAME/PASSWORD.")
        return 2

    cache = CacheStore(settings.cache_dir, settings.zarr_store)
    sst = SSTProvider(settings=settings, cache=cache)

    today = date.today()
    start = today - timedelta(days=1)
    end = today - timedelta(days=1)

    log.info("Fetching SST for %s (cache merge handled by provider)", start)
    sst.load_range(start, end)

    # Re-run detection on trailing window.
    window_start = today - timedelta(days=60)
    window_end = today
    log.info("Re-detecting MHWs for %s → %s", window_start, window_end)
    ds = xr.open_zarr(cache.zarr_path)
    ds = ds.sel(time=slice(str(window_start), str(window_end)))

    var_name = next((v for v in ("analysed_sst", "sst", "thetao") if v in ds.data_vars), None)
    if var_name is None:
        log.error("No SST variable in Zarr store: %s", list(ds.data_vars))
        return 3
    da = ds[var_name]
    events = detect_cube(da, clim_period=(settings.clim_start, settings.clim_end))

    geojson = events_to_geojson(events)
    out = cache.cache_dir / "events_recent.geojson"
    out.write_text(json.dumps(geojson), encoding="utf-8")
    log.info("Wrote %d events → %s", len(events), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
