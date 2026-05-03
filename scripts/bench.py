"""Latency benchmark for MHEAT REST endpoints.

Runs 20 warmed calls against a running MHEAT instance, computes P50/P95/P99,
and writes a Markdown table to ``docs/performance.md``.

Usage:

    python scripts/bench.py --base-url http://localhost:8000 --iterations 20

Results table columns:
    endpoint | samples | P50 (ms) | P95 (ms) | P99 (ms) | max (ms)

The warm-up phase executes ``n`` throw-away requests against each endpoint
before the measured phase so cold-cache artefacts (lazy xarray open, numpy
JIT, cluster cache, etc.) don't skew P50.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List

import urllib.request


ENDPOINTS: Dict[str, str] = {
    "GET /api/events": "/api/events?start=2022-05-15&end=2022-09-15",
    "GET /api/events.csv": "/api/events.csv?start=2022-05-15&end=2022-09-15",
    "GET /api/events/{id}/series": "/api/events/mhw-cluster-0001/series?lon=14.5&lat=41.5",
    "GET /api/anomaly": "/api/anomaly?date=2022-07-20",
    "GET /api/overlays/mpa": "/api/overlays/mpa",
    "GET /api/ogcapi/collections": "/api/ogcapi/collections",
    "GET /api/ogcapi/items (mhw-events)": "/api/ogcapi/collections/mhw-events/items?limit=100",
}


def _hit(url: str) -> float:
    """Return wall-clock ms for one GET request; raises on HTTP error."""
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        r.read()
        if r.status >= 400:
            raise RuntimeError(f"{url} → {r.status}")
    return (time.perf_counter() - t0) * 1000.0


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def run(base: str, iterations: int, warmup: int) -> List[Dict[str, float]]:
    """Execute the bench and return a list of per-endpoint stats dicts."""
    results = []
    for label, path in ENDPOINTS.items():
        url = base.rstrip("/") + path
        # Warm up
        for _ in range(warmup):
            try:
                _hit(url)
            except Exception as e:  # noqa: BLE001
                print(f"warm-up failed for {label}: {e}", file=sys.stderr)
                break

        # Measured
        samples: List[float] = []
        for _ in range(iterations):
            try:
                samples.append(_hit(url))
            except Exception as e:  # noqa: BLE001
                print(f"measured call failed for {label}: {e}", file=sys.stderr)

        if not samples:
            results.append({"label": label, "ok": False})
            continue
        results.append(
            {
                "label": label,
                "ok": True,
                "n": len(samples),
                "p50": _percentile(samples, 50),
                "p95": _percentile(samples, 95),
                "p99": _percentile(samples, 99),
                "max": max(samples),
                "mean": statistics.fmean(samples),
            }
        )
    return results


def render(rows: List[Dict[str, float]]) -> str:
    """Render a results list as a GitHub-flavoured Markdown table."""
    lines = [
        "# MHEAT performance benchmark",
        "",
        "Measured with `scripts/bench.py` (20 warmed calls per endpoint).",
        "Target: **P95 <= 2000 ms** on a laptop against the baked Zarr cache.",
        "",
        "| endpoint | samples | P50 (ms) | P95 (ms) | P99 (ms) | max (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        if not r.get("ok"):
            lines.append(f"| {r['label']} | — | FAILED | FAILED | FAILED | FAILED |")
            continue
        lines.append(
            f"| {r['label']} | {int(r['n'])} | {r['p50']:.0f} | {r['p95']:.0f} | "
            f"{r['p99']:.0f} | {r['max']:.0f} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--iterations", type=int, default=20)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "performance.md"),
    )
    args = p.parse_args(argv)

    rows = run(args.base_url, args.iterations, args.warmup)
    md = render(rows)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
