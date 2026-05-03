#!/usr/bin/env bash
# Daily cron entry: pull yesterday's NRT slice into the SST cube.
# Designed to be called from `crontab -e` once a day after Copernicus
# publishes the previous day's L4 NRT product (typically by 04:00 UTC).
#
# Logs append to out/cron_daily.log. Exits non-zero on failure so cron
# can email the user (if MAILTO is set in the crontab header).
#
# Usage in crontab (set MHEAT_REPO to your local repo path):
#   0 6 * * * MHEAT_REPO=/path/to/mheat /path/to/mheat/scripts/cron_daily_pull.sh
#
set -euo pipefail

# Resolve the repo root: prefer $MHEAT_REPO env var, fall back to script's
# parent dir so it works in either invocation style.
REPO="${MHEAT_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG="$REPO/out/cron_daily.log"
mkdir -p "$REPO/out"

cd "$REPO"

# Header line per run so it's easy to scan.
echo "" >>"$LOG"
echo "==== $(date -Iseconds) cron_daily_pull start ====" >>"$LOG"

# Load Copernicus credentials from .env. The python copernicusmarine SDK
# reads COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD from the environment.
set -a
# shellcheck disable=SC1091
source "$REPO/.env"
set +a

# Pull last-3-days window (yesterday + the 2 days before, cheap insurance
# against weekend backfills + late-publishing days). Uses the in-tree
# SSTProvider.load_range which already does the cache-first / lazy-fill
# logic so this is idempotent.
"$REPO/.venv/bin/python" - <<PY >>"$LOG" 2>&1
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path("$REPO") / "backend"))

from app.cache import CacheStore
from app.config import Settings
from app.sst import SSTProvider

settings = Settings()
cache = CacheStore(settings.cache_dir, settings.zarr_store)
provider = SSTProvider(settings=settings, cache=cache)

today = date.today()
start = today - timedelta(days=3)
end = today
print(f"[cron] pulling {start}..{end} from CMS NRT…", flush=True)
provider.load_range(start, end)
ext = provider.cube_extent()
if ext:
    print(f"[cron] cube now {ext[0]} → {ext[1]} "
          f"({(ext[1] - ext[0]).days + 1} day-span)", flush=True)
else:
    print(f"[cron] WARN: cube extent unreadable after pull", flush=True)
PY

STATUS=$?
echo "==== $(date -Iseconds) cron_daily_pull exit=$STATUS ====" >>"$LOG"
exit $STATUS
