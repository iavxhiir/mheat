#!/usr/bin/env bash
# MHEAT — prepare the submission bundle.
#
# Superset of ./run.sh: runs every CI gate AND regenerates every
# artefact in out/ (reproducibility manifest, ARCO Zarr, STAC tree,
# OpenAPI baseline, performance bench). Writes a ready-to-read
# summary to out/PREPARE_SUMMARY.md.
#
#   ./prepare.sh                # default: gates + artefacts (~6 min)
#   ./prepare.sh --include-slow # + performance bench
#   ./prepare.sh --skip-audits  # offline
#   ./prepare.sh --keep-going   # don't stop at first failure
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  local candidates=("${PYTHON:-}" python python3 py)
  for cand in "${candidates[@]}"; do
    [[ -z "$cand" ]] && continue
    if out=$("$cand" --version 2>&1) && [[ "$out" == Python\ 3.* ]]; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}

if ! PY=$(find_python); then
  echo "ERROR: Python 3.11+ required." >&2
  exit 2
fi

VENV=".venv"
if [[ ! -d "$VENV" ]]; then
  echo "[prepare.sh] First run — creating $VENV and installing deps..."
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
if [[ -f "$VENV/bin/activate" ]]; then
  source "$VENV/bin/activate"
elif [[ -f "$VENV/Scripts/activate" ]]; then
  source "$VENV/Scripts/activate"
fi
python -m pip install --upgrade pip --quiet

REQ="backend/requirements-dev.txt"
STAMP="$VENV/.mheat-install-stamp"
if [[ ! -f "$STAMP" ]] || [[ "$REQ" -nt "$STAMP" ]]; then
  echo "[prepare.sh] Installing backend deps..."
  python -m pip install --quiet -r "$REQ"
  touch "$STAMP"
fi

exec python scripts/prepare_submission.py "$@"
