#!/usr/bin/env bash
# MHEAT — single all-in-one entry point.
#
# Runs the full story on one command:
#   1. env check (python / node / npm / docker)
#   2. create .venv + install backend dev deps
#   3. install frontend deps
#   4. ruff lint
#   5. mypy type-check
#   6. backend tests + coverage gate
#   7. frontend ESLint
#   8. frontend Vite build
#   9. Vitest (unit tests + coverage)
#  10. pip-audit (strict, filters git+ deps)
#  11. npm audit (prod, HIGH/CRITICAL block)
#  12. freeze OpenAPI baseline
#  13. regenerate reproducibility manifest
#  14. export ARCO Zarr cube
#  15. build STAC tree (dry-run)
#  16. in-process performance benchmark
#  17. Docker image build (if daemon available)
#  18. offer to start the service on http://localhost:8000
#
# Flags:
#   --fast         skip slow steps (bench + docker)
#   --skip-audits  skip pip-audit + npm audit
#   --no-install   reuse existing .venv / node_modules
#   --keep-going   don't stop at first failing gate
#   --serve        at the end, auto-start the service (blocks until Ctrl+C)
#   --help         this help
set -euo pipefail
cd "$(dirname "$0")"

FAST=0
NO_INSTALL=0
SERVE=0
PASSTHROUGH=()

for arg in "$@"; do
  case "$arg" in
    --fast)        FAST=1 ;;
    --no-install)  NO_INSTALL=1 ;;
    --serve)       SERVE=1 ;;
    --skip-audits) PASSTHROUGH+=("--skip-audits") ;;
    --keep-going)  PASSTHROUGH+=("--keep-going") ;;
    --help|-h)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)             PASSTHROUGH+=("$arg") ;;
  esac
done

[[ $FAST -eq 0 ]] && PASSTHROUGH+=("--include-slow")

find_python() {
  # On Windows, `python3` is a Microsoft Store stub that prints a help
  # message and exits non-zero. Probe each candidate by running --version
  # and checking the output starts with "Python 3.".
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
  echo "ERROR: Python 3.11+ required. Install python3 from python.org, then re-run." >&2
  exit 2
fi

banner() {
  local bar; bar="$(printf '━%.0s' $(seq 1 ${#1}))"
  printf '\n\033[1;36m%s\n  %s\n%s\033[0m\n' "$bar" "$1" "$bar"
}

# ---- 1. env check --------------------------------------------------------
banner "MHEAT — all-in-one runner"
echo "  • python:  $("$PY" --version 2>&1)"
if command -v node >/dev/null 2>&1; then echo "  • node:    $(node --version)"; fi
if command -v npm  >/dev/null 2>&1; then echo "  • npm:     $(npm --version)"; fi
if command -v docker >/dev/null 2>&1; then echo "  • docker:  $(docker --version 2>&1 | head -1)"; fi

# ---- 2. venv bootstrap ---------------------------------------------------
VENV=".venv"
if [[ $NO_INSTALL -eq 0 ]]; then
  if [[ ! -d "$VENV" ]]; then
    banner "Creating .venv/ and installing backend dev deps (first run)"
    "$PY" -m venv "$VENV"
  fi
fi

# shellcheck disable=SC1091
if [[ -f "$VENV/bin/activate" ]]; then
  source "$VENV/bin/activate"
elif [[ -f "$VENV/Scripts/activate" ]]; then
  source "$VENV/Scripts/activate"
fi
python -m pip install --upgrade pip --quiet

if [[ $NO_INSTALL -eq 0 ]]; then
  REQ="backend/requirements-dev.txt"
  STAMP="$VENV/.mheat-install-stamp"
  if [[ ! -f "$STAMP" ]] || [[ "$REQ" -nt "$STAMP" ]]; then
    banner "Installing backend dev deps"
    python -m pip install --quiet -r "$REQ"
    touch "$STAMP"
  fi
fi

# ---- 3-16. the orchestrator handles every gate + artefact ---------------
banner "Running every gate + regenerating every artefact"
if python scripts/prepare_submission.py "${PASSTHROUGH[@]}"; then
  PREPARE_EXIT=0
else
  PREPARE_EXIT=$?
fi

# ---- 17. Docker build (only when a daemon is reachable) -----------------
DOCKER_NOTE="(skipped — docker daemon not reachable)"
if [[ $FAST -eq 0 ]] && command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    banner "Docker build (multi-stage, <500 MiB)"
    if docker build -t mheat:local . ; then
      SIZE=$(docker image inspect mheat:local --format '{{.Size}}' 2>/dev/null || echo 0)
      DOCKER_NOTE="built mheat:local ($((SIZE / 1024 / 1024)) MiB)"
    else
      DOCKER_NOTE="docker build FAILED"
    fi
  fi
fi
banner "Docker: $DOCKER_NOTE"

# ---- 18. offer to start the server --------------------------------------
if [[ $PREPARE_EXIT -eq 0 ]]; then
  banner "Everything green. Summary at out/PREPARE_SUMMARY.md"
  if [[ $SERVE -eq 1 ]]; then
    echo "Starting the service (blocks until Ctrl+C)..."
    exec ./start.sh
  fi
  cat <<'MSG'

  To start the service now:
    ./start.sh                 # DEMO_MODE on, http://localhost:8000
    DEMO_MODE=false ./start.sh # live mode (requires Copernicus creds)

  To ship a release:
    git tag v0.4.1 && git push --tags

MSG
  exit 0
fi

banner "Some gates failed — see the tail above and out/PREPARE_SUMMARY.md"
exit "$PREPARE_EXIT"
