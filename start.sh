#!/usr/bin/env bash
# MHEAT — start the service locally.
#
# Usage:
#   ./start.sh              # build frontend + run FastAPI on :8000 in DEMO_MODE
#   ./start.sh --no-frontend  # API only (skip the Vite build)
#   ./start.sh --port 9000    # custom port
#
# Stop with Ctrl+C. First run installs deps into .venv/; later runs reuse it.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
BUILD_FRONTEND=1
for arg in "$@"; do
  case "$arg" in
    --no-frontend) BUILD_FRONTEND=0 ;;
    --port) shift ;;
    --port=*) PORT="${arg#*=}" ;;
  esac
done

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

# Bootstrap the venv the same way ./run.sh does.
VENV=".venv"
if [[ ! -d "$VENV" ]]; then
  echo "[start.sh] First run — creating $VENV and installing deps..."
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
  echo "[start.sh] Installing backend deps..."
  python -m pip install --quiet -r "$REQ"
  touch "$STAMP"
fi

# Build the frontend unless the user asked to skip it.
if [[ $BUILD_FRONTEND -eq 1 ]] && command -v npm >/dev/null 2>&1; then
  if [[ ! -d "frontend/dist" ]] || [[ "frontend/src" -nt "frontend/dist" ]]; then
    echo "[start.sh] Building frontend (Vite)..."
    (cd frontend && npm install --no-audit --no-fund --loglevel=error >/dev/null && npm run build)
  fi
  export FRONTEND_DIR="$PWD/frontend/dist"
fi

export DEMO_MODE="${DEMO_MODE:-true}"
export PORT
echo ""
echo "────────────────────────────────────────────────────────"
echo "  MHEAT starting (DEMO_MODE=$DEMO_MODE)"
echo "    dashboard: http://localhost:$PORT/"
echo "    API docs:  http://localhost:$PORT/api/docs"
echo "    health:    http://localhost:$PORT/api/health"
echo "    (Ctrl+C to stop)"
echo "────────────────────────────────────────────────────────"
echo ""

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" \
  --app-dir backend --reload
