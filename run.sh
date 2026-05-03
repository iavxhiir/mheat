#!/usr/bin/env bash
# MHEAT — one-command entry point.
#
#   ./run.sh                    # env + lint + tests + reproduce + STAC  (~5 min)
#   ./run.sh --quick            # lint + tests only                       (~2 min)
#   ./run.sh --include-slow     # + bench + docker build
#   ./run.sh --list             # every available phase
#   ./run.sh --help             # full help
#
# Bootstraps .venv/ on first run if it doesn't exist, installs the backend
# dev deps, then calls scripts/run_all.py. Subsequent runs reuse the venv
# and are fast.
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
    # On Windows `python3` is often a Microsoft Store stub. Probe each
    # candidate by actually running --version.
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
    echo "ERROR: Python 3.11+ is required. Install it, then re-run ./run.sh"
    exit 2
fi

VENV=".venv"
if [[ ! -d "$VENV" ]]; then
    echo "[run.sh] First run — creating $VENV and installing backend dev deps..."
    "$PY" -m venv "$VENV"
fi

# shellcheck disable=SC1091
if [[ -f "$VENV/bin/activate" ]]; then
    source "$VENV/bin/activate"
elif [[ -f "$VENV/Scripts/activate" ]]; then
    # Git-Bash on Windows
    source "$VENV/Scripts/activate"
else
    echo "ERROR: couldn't find activate script under $VENV"
    exit 2
fi

python -m pip install --upgrade pip --quiet
# Install deps on first run, or if the requirements-dev.txt is newer than
# the venv (simple heuristic — keeps steady-state runs fast).
REQ="backend/requirements-dev.txt"
STAMP="$VENV/.mheat-install-stamp"
if [[ ! -f "$STAMP" ]] || [[ "$REQ" -nt "$STAMP" ]]; then
    echo "[run.sh] Installing $REQ ..."
    python -m pip install --quiet -r "$REQ"
    touch "$STAMP"
fi

exec python scripts/run_all.py "$@"
