"""Post-deploy smoke test — curls the public endpoints and asserts 200s.

Intended to run immediately after a Helm rollout or ``docker compose up``
to confirm the live service is reachable and serving each contract. Also
usable as the heartbeat step of a cron-job healthchecker.

Usage::

    python scripts/smoke.py --base-url http://localhost:8000
    python scripts/smoke.py --base-url https://mheat.edito.example --timeout 15

Exit codes: 0 if every probe passed, 1 if any failed.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

# (path, expected_content_type_prefix, optional_body_substring)
PROBES: list[tuple[str, str, Optional[str]]] = [
    ("/api/health", "application/json", '"status":"ok"'),
    ("/api/readyz", "application/json", '"status":'),
    ("/api/events?start=2022-07-01&end=2022-08-15", "application/json", "FeatureCollection"),
    ("/api/events.csv?start=2022-07-01&end=2022-08-15", "text/csv", "event_id"),
    ("/api/events.parquet?start=2022-07-01&end=2022-08-15", "application/vnd.apache.parquet", "PAR1"),
    ("/api/anomaly?date=2022-07-20", "image/png", None),
    ("/api/stac/collections", "application/json", "stac_version"),
    ("/api/ogcapi", "application/json", "links"),
    ("/api/ogcapi/collections/mhw-events/items?limit=10", "application/json", "FeatureCollection"),
    ("/api/processes", "application/json", "mhw-detect"),
    ("/api/processes/conformance", "application/json", "ogcapi-processes"),
]


@dataclass
class ProbeResult:
    path: str
    status: int
    elapsed_ms: float
    ok: bool
    note: str = ""


def _hit(base: str, path: str, timeout: float) -> ProbeResult:
    url = base.rstrip("/") + path
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
            ct = resp.headers.get("content-type", "")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        ct = e.headers.get("content-type", "") if e.headers else ""
        status = e.code
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(path=path, status=0, elapsed_ms=(time.perf_counter() - t0) * 1000,
                           ok=False, note=str(exc))

    elapsed = (time.perf_counter() - t0) * 1000

    # Expected content-type check.
    expected_ct = next((p[1] for p in PROBES if p[0] == path), "")
    if not ct.startswith(expected_ct):
        return ProbeResult(path=path, status=status, elapsed_ms=elapsed, ok=False,
                           note=f"content-type {ct!r} != expected {expected_ct!r}")

    # Body substring check (if declared).
    needle = next((p[2] for p in PROBES if p[0] == path), None)
    if needle:
        look_in = body[:4096]  # head only — enough for JSON / CSV / parquet header
        needle_bytes = needle.encode() if isinstance(needle, str) else needle
        if needle_bytes not in look_in:
            return ProbeResult(path=path, status=status, elapsed_ms=elapsed, ok=False,
                               note=f"body missing expected substring {needle!r}")

    ok = 200 <= status < 300
    return ProbeResult(path=path, status=status, elapsed_ms=elapsed, ok=ok)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--timeout", type=float, default=15.0, help="Per-probe timeout in seconds.")
    p.add_argument("--quiet", action="store_true")
    ns = p.parse_args(argv)

    results: list[ProbeResult] = []
    for path, _expected_ct, _needle in PROBES:
        r = _hit(ns.base_url, path, ns.timeout)
        results.append(r)
        if not ns.quiet:
            tag = "✓" if r.ok else "✗"
            note = f"  ({r.note})" if r.note else ""
            print(f"  {tag} {r.status:>3}  {r.elapsed_ms:6.1f} ms  {path}{note}")

    failed = [r for r in results if not r.ok]
    ok = [r for r in results if r.ok]
    if not ns.quiet:
        print()
    print(f"Smoke: {len(ok)}/{len(results)} passed in {sum(r.elapsed_ms for r in results):.0f} ms total")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
