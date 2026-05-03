"""substrate — seabed substrate × MHW vulnerability (standalone CLI).

Self-contained. Only numpy is required; matplotlib is optional for
``--plot``. No MHEAT coupling.

Usage::

    python substrate.py                     # default seed, print table
    python substrate.py --seed 7            # different synthetic scenario
    python substrate.py --json out.json     # machine-readable output
    python substrate.py --plot out.png      # saves a substrate map
    python substrate.py --help              # every option
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

try:  # Windows cp1252 can't render ≥ / █ / ×.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


VULNERABILITY = {"biogenic": 1.0, "rock": 0.7, "sand": 0.3, "mud": 0.1}
CLASS_ORDER = ("rock", "sand", "mud", "biogenic")
CLASS_COLOR = {"rock": "#6b4e3d", "sand": "#f2d88f",
               "mud": "#5a7d8f", "biogenic": "#2a9d5f"}


# ---------- synthesis ------------------------------------------------------
def substrate_grid(rng: np.random.Generator,
                   step_deg: float = 0.1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lon = np.arange(-5.0, 37.0, step_deg)
    lat = np.arange(30.0, 47.0, step_deg)
    lon2d, lat2d = np.meshgrid(lon, lat)

    grid = np.full(lon2d.shape, "sand", dtype=object)

    centre_dist = np.sqrt((lon2d - 16.0) ** 2 + ((lat2d - 39.0) * 2.0) ** 2)
    grid[centre_dist < 5.0] = "mud"

    rock_band = ((lon2d > 6) & (lon2d < 10) & (lat2d > 43)) | \
                ((lon2d > 14) & (lon2d < 20) & (lat2d > 42) & (lat2d < 45))
    grid[rock_band & (rng.random(lon2d.shape) < 0.6)] = "rock"

    biogenic_band = ((lon2d > 12.5) & (lon2d < 15) & (lat2d > 43.5) & (lat2d < 45)) | \
                    ((lon2d > 1) & (lon2d < 4) & (lat2d > 39) & (lat2d < 40.5))
    grid[biogenic_band & (rng.random(lon2d.shape) < 0.5)] = "biogenic"

    return lon, lat, grid


def synth_events() -> list[dict]:
    return [
        {"id": "mhw-01", "name": "N Adriatic 2022", "lon": 13.5, "lat": 44.5, "sx": 1.0, "sy": 0.6},
        {"id": "mhw-02", "name": "Ligurian 2022",  "lon":  8.0, "lat": 43.5, "sx": 1.2, "sy": 0.8},
        {"id": "mhw-03", "name": "Balearic 2024",  "lon":  2.5, "lat": 39.5, "sx": 1.5, "sy": 0.7},
        {"id": "mhw-04", "name": "Central basin",  "lon": 18.0, "lat": 37.0, "sx": 2.0, "sy": 1.2},
        {"id": "mhw-05", "name": "Aegean 2023",    "lon": 24.5, "lat": 37.5, "sx": 1.8, "sy": 1.0},
    ]


# ---------- analysis -------------------------------------------------------
def analyse(seed: int, step_deg: float = 0.1) -> dict:
    rng = np.random.default_rng(seed)
    lon, lat, grid = substrate_grid(rng, step_deg)
    lon2d, lat2d = np.meshgrid(lon, lat)
    events = synth_events()

    basin_shares = {c: round(float((grid == c).mean()), 3) for c in CLASS_ORDER}

    per_event = []
    for e in events:
        inside = ((lon2d - e["lon"]) / e["sx"]) ** 2 + ((lat2d - e["lat"]) / e["sy"]) ** 2 <= 1.0
        n = int(inside.sum())
        if n == 0:
            per_event.append({**e, "cells": 0, "shares": {c: 0.0 for c in CLASS_ORDER},
                              "vuln_score": 0.0})
            continue
        cls_in = grid[inside]
        shares = {c: round(float((cls_in == c).mean()), 3) for c in CLASS_ORDER}
        score = sum(shares[c] * VULNERABILITY[c] for c in CLASS_ORDER)
        per_event.append({**e, "cells": n, "shares": shares,
                          "vuln_score": round(score, 3)})

    ranked = sorted(per_event, key=lambda p: -p["vuln_score"])
    return {
        "seed": seed,
        "grid_shape": [int(grid.shape[1]), int(grid.shape[0])],
        "classes": list(CLASS_ORDER),
        "vulnerability_weights": VULNERABILITY,
        "basin_shares": basin_shares,
        "n_events": len(events),
        "per_event": per_event,
        "ranked_by_vulnerability": [(p["name"], p["vuln_score"]) for p in ranked],
        "_synth": {
            "lon_min": float(lon.min()), "lon_max": float(lon.max()),
            "lat_min": float(lat.min()), "lat_max": float(lat.max()),
            "grid": grid.tolist(),
        },
    }


# ---------- rendering ------------------------------------------------------
def print_report(r: dict) -> None:
    print(f"Prototype 3 — SEABED SUBSTRATE × MHW\n{'-' * 60}")
    print(f"Grid: {r['grid_shape'][0]} × {r['grid_shape'][1]} cells across "
          f"{len(r['classes'])} classes")
    for c in CLASS_ORDER:
        print(f"  basin share {c:<9} {r['basin_shares'][c]:6.2f}   "
              f"vulnerability {VULNERABILITY[c]:.1f}")
    print()
    print(f"{'event':<9} {'name':<20} {'cells':>6} "
          f"{'rock':>5} {'sand':>5} {'mud':>5} {'bio':>5} {'vuln_score':>11}")
    print("-" * 75)
    for p in r["per_event"]:
        s = p["shares"]
        print(f"{p['id']:<9} {p['name']:<20} {p['cells']:6d} "
              f"{s['rock']:5.2f} {s['sand']:5.2f} {s['mud']:5.2f} "
              f"{s['biogenic']:5.2f} {p['vuln_score']:11.3f}")

    print()
    print("Events ranked by substrate-weighted vulnerability (high = fragile substrates):")
    for i, (name, s) in enumerate(r["ranked_by_vulnerability"], 1):
        bar = "█" * int(round(s * 40))
        print(f"  {i}. {name:<20} {s:5.3f}  {bar}")
    print()
    print("Takeaway: raw footprint area would have ranked these differently.")
    print("Substrate weighting promotes events over biogenic / rocky habitat.")


def save_plot(r: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    grid = np.array(r["_synth"]["grid"])
    idx = np.zeros(grid.shape, dtype=int)
    for i, c in enumerate(CLASS_ORDER):
        idx[grid == c] = i
    cmap = mcolors.ListedColormap([CLASS_COLOR[c] for c in CLASS_ORDER])
    extent = [r["_synth"]["lon_min"], r["_synth"]["lon_max"],
              r["_synth"]["lat_min"], r["_synth"]["lat_max"]]

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.imshow(idx, origin="lower", extent=extent, aspect="auto",
              cmap=cmap, vmin=-0.5, vmax=len(CLASS_ORDER) - 0.5)
    for p in r["per_event"]:
        ax.scatter(p["lon"], p["lat"], s=200,
                   facecolors="none", edgecolors="black", linewidths=1.8)
        ax.annotate(p["id"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=8)
    handles = [plt.Line2D([0], [0], marker="s", color="w", label=c,
                          markerfacecolor=CLASS_COLOR[c], markersize=10)
               for c in CLASS_ORDER]
    ax.legend(handles=handles, loc="lower right", fontsize=8)
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title(f"substrate prototype — seed {r['seed']}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------- CLI ------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=2023,
                   help="RNG seed for the synthetic scenario (default: 2023).")
    p.add_argument("--step-deg", type=float, default=0.1,
                   help="Grid resolution in degrees (default: 0.1).")
    p.add_argument("--json", metavar="PATH", type=Path,
                   help="Write the analysis as JSON to this path.")
    p.add_argument("--plot", metavar="PATH", type=Path,
                   help="Write a PNG map (requires matplotlib).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the human-readable table.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    r = analyse(args.seed, args.step_deg)
    if not args.quiet:
        print_report(r)
    if args.json:
        payload = {k: v for k, v in r.items() if not k.startswith("_")}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON → {args.json}")
    if args.plot:
        args.plot.parent.mkdir(parents=True, exist_ok=True)
        save_plot(r, args.plot)
        print(f"PNG  → {args.plot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
