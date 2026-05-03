"""Regenerate ``docs/api/openapi.baseline.json`` from the live app.

Run whenever an OpenAPI change is deliberate (added endpoint, bumped
spec version). The contract-diff test in
``backend/tests/test_openapi_contract_freeze.py`` will then consider the
new shape authoritative.

Usage::

    python scripts/freeze_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUT = ROOT / "docs" / "api" / "openapi.baseline.json"


def main() -> int:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    from fastapi.testclient import TestClient

    from app.main import app

    spec = TestClient(app).get("/api/openapi.json").json()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
