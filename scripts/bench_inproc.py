"""In-process latency benchmark — no uvicorn / network required.

Mirrors ``scripts/bench.py`` but drives the FastAPI app via
``fastapi.testclient.TestClient``, so it runs in CI and reviewer
sandboxes without having to start a separate service.

Adds two extras over the networked bench:

* The new ``/api/events.parquet`` endpoint.
* A cold-vs-warm split on ``/api/events`` showing the effect of the
  response cache + ETag handling.

Writes ``docs/performance.md``.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _bootstrap() -> None:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


ENDPOINTS: Dict[str, str] = {
    "GET /api/events":               "/api/events?start=2022-05-15&end=2022-09-15",
    "GET /api/events.csv":           "/api/events.csv?start=2022-05-15&end=2022-09-15",
    "GET /api/events.parquet":       "/api/events.parquet?start=2022-05-15&end=2022-09-15",
    "GET /api/anomaly":              "/api/anomaly?date=2022-07-20",
    "GET /api/overlays/mpa":         "/api/overlays/mpa",
    "GET /api/ogcapi/collections":   "/api/ogcapi/collections",
    "GET /api/ogcapi items (mhw)":   "/api/ogcapi/collections/mhw-events/items?limit=100",
    "GET /api/stac/collections":     "/api/stac/collections",
    "GET /api/health":               "/api/health",
    "GET /api/readyz":               "/api/readyz",
}


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def _measure(client, url: str, n: int, warmup: int) -> Dict[str, float]:
    for _ in range(warmup):
        r = client.get(url)
        r.raise_for_status()
    samples: List[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = client.get(url)
        r.raise_for_status()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {
        "n": len(samples),
        "p50": _percentile(samples, 50),
        "p95": _percentile(samples, 95),
        "p99": _percentile(samples, 99),
        "max": max(samples),
        "mean": statistics.fmean(samples),
    }


def _cold_vs_warm(client, url: str) -> Dict[str, float]:
    """Single-shot cold (cache invalidated) vs warm (cache hit) measurement."""
    from app.routers.events import clear_response_cache

    clear_response_cache()
    t0 = time.perf_counter()
    client.get(url).raise_for_status()
    cold = (time.perf_counter() - t0) * 1000.0

    # Warm — the cache now has the entry.
    t0 = time.perf_counter()
    client.get(url).raise_for_status()
    warm = (time.perf_counter() - t0) * 1000.0

    # 304 path — client sends the If-None-Match header. httpx treats 304 as a
    # redirect status by default (it is, per RFC 9110 §15.4.5), so we only
    # assert the raw status code here instead of raise_for_status().
    etag = client.get(url).headers["ETag"]
    t0 = time.perf_counter()
    r = client.get(url, headers={"If-None-Match": etag})
    not_modified = (time.perf_counter() - t0) * 1000.0
    assert r.status_code == 304, f"expected 304, got {r.status_code}"

    return {"cold": cold, "warm": warm, "not_modified": not_modified}


def render(endpoint_rows: Dict[str, Dict[str, float]], cold_warm: Dict[str, float]) -> str:
    lines = [
        "# MHEAT performance benchmark",
        "",
        "Measured in-process with `scripts/bench_inproc.py` (TestClient, no",
        "uvicorn socket). 20 warmed calls per endpoint on a developer laptop",
        "against the baked SST + climatology Zarr cache. Target: **P95 ≤ 2000 ms**.",
        "",
        "## Per-endpoint latency",
        "",
        "| endpoint | samples | P50 (ms) | P95 (ms) | P99 (ms) | max (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, r in endpoint_rows.items():
        lines.append(
            f"| {label} | {int(r['n'])} | {r['p50']:.0f} | {r['p95']:.0f} |"
            f" {r['p99']:.0f} | {r['max']:.0f} |"
        )

    lines += [
        "",
        "## ETag cold / warm / 304 split on `/api/events`",
        "",
        "Demonstrates the effect of the response cache + ETag handling",
        "(Pass 53 of the changelog). `cold` runs the full Hobday pipeline;",
        "`warm` serves from the in-process cache; `304` is a client sending",
        "back the matching `If-None-Match` header.",
        "",
        "| path | latency (ms) |",
        "| --- | ---: |",
        f"| cold (cache empty, detection runs)        | {cold_warm['cold']:.1f} |",
        f"| warm (cache hit, body re-emitted)         | {cold_warm['warm']:.1f} |",
        f"| 304 Not Modified (If-None-Match matched)  | {cold_warm['not_modified']:.1f} |",
        "",
        "Speed-up vs cold: "
        f"warm ≈ {cold_warm['cold']/max(cold_warm['warm'],0.01):.0f}×, "
        f"304 ≈ {cold_warm['cold']/max(cold_warm['not_modified'],0.01):.0f}×.",
        "",
    ]
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    _bootstrap()
    from fastapi.testclient import TestClient

    from app.main import app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "performance.md"),
    )
    args = parser.parse_args(argv)

    client = TestClient(app)
    rows: Dict[str, Dict[str, float]] = {}
    for label, path in ENDPOINTS.items():
        rows[label] = _measure(client, path, args.iterations, args.warmup)

    cw = _cold_vs_warm(client, "/api/events?start=2022-05-15&end=2022-09-15")
    md = render(rows, cw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    # Best-effort stdout echo — Windows cp1252 can choke on ≤ / ×.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    print(md)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
