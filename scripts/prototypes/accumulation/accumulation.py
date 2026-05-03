"""accumulation — sediment-accumulation × MHW overlap (standalone CLI).

Self-contained. Only numpy is required; matplotlib is optional for
``--plot``. No MHEAT coupling.

Usage::

    python accumulation.py                     # default seed, print table
    python accumulation.py --seed 7            # different synthetic scenario
    python accumulation.py --json out.json     # machine-readable output
    python accumulation.py --plot out.png      # saves a map of hotspots
    python accumulation.py --help              # every option
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


# ---------- synthesis ------------------------------------------------------
def sedimentation_grid(rng: np.random.Generator,
                       step_deg: float = 0.1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lon = np.arange(-5.0, 37.0, step_deg)
    lat = np.arange(30.0, 47.0, step_deg)
    lon2d, lat2d = np.meshgrid(lon, lat)
    rate = 0.3 + 0.2 * rng.random(lon2d.shape)  # background 0.3-0.5 mm/yr
    hotspots = [
        (12.5, 45.1, 6.0, 0.6, 0.5),   # Po delta
        (4.8, 43.3, 5.0, 0.8, 0.6),    # Rhône delta
        (31.5, 31.5, 8.0, 0.9, 0.7),   # Nile delta
    ]
    for lon0, lat0, peak, sx, sy in hotspots:
        rate += peak * np.exp(-(((lon2d - lon0) / sx) ** 2 + ((lat2d - lat0) / sy) ** 2))
    return lon, lat, rate


def synth_events(rng: np.random.Generator, n: int = 6) -> list[dict]:
    centres = [
        (6.0, 42.5, "Ligurian 2022"),
        (12.0, 43.7, "Central Adriatic 2022"),
        (18.0, 38.5, "Ionian 2024"),
        (14.8, 44.6, "Northern Adriatic 2023"),
        (4.5, 43.1, "Gulf of Lion 2023"),
        (31.0, 32.5, "SE Med 2024"),
    ]
    return [
        {
            "id": f"mhw-{i:02d}",
            "name": name,
            "lon": lon,
            "lat": lat,
            "sx": round(float(rng.uniform(0.7, 1.8)), 3),
            "sy": round(float(rng.uniform(0.5, 1.2)), 3),
        }
        for i, (lon, lat, name) in enumerate(centres[:n])
    ]


# ---------- analysis -------------------------------------------------------
def classify(rate: np.ndarray) -> np.ndarray:
    return np.select([rate < 1.0, rate < 2.0], ["low", "medium"], default="high")


def analyse(seed: int, step_deg: float = 0.1, n_events: int = 6) -> dict:
    rng = np.random.default_rng(seed)
    lon, lat, rate = sedimentation_grid(rng, step_deg)
    lon2d, lat2d = np.meshgrid(lon, lat)
    classes = classify(rate)
    events = synth_events(rng, n_events)

    basin_shares = {c: float((classes == c).mean()) for c in ("low", "medium", "high")}

    per_event = []
    for e in events:
        inside = ((lon2d - e["lon"]) / e["sx"]) ** 2 + ((lat2d - e["lat"]) / e["sy"]) ** 2 <= 1.0
        n_cells = int(inside.sum())
        if n_cells == 0:
            per_event.append({**e, "cells": 0, "low": 0.0, "medium": 0.0, "high": 0.0, "mean_rate": 0.0})
            continue
        cls_in = classes[inside]
        r_in = rate[inside]
        per_event.append({
            **e,
            "cells": n_cells,
            "low": round(float((cls_in == "low").mean()), 3),
            "medium": round(float((cls_in == "medium").mean()), 3),
            "high": round(float((cls_in == "high").mean()), 3),
            "mean_rate": round(float(r_in.mean()), 2),
        })

    ranked = sorted(per_event, key=lambda p: -p["high"])
    return {
        "seed": seed,
        "grid_cells": int(rate.size),
        "grid_shape": [int(rate.shape[1]), int(rate.shape[0])],
        "rate_min": round(float(rate.min()), 2),
        "rate_max": round(float(rate.max()), 2),
        "rate_median": round(float(np.median(rate)), 2),
        "basin_shares": {k: round(v, 3) for k, v in basin_shares.items()},
        "n_events": len(events),
        "per_event": per_event,
        "ranked_by_high_overlap": [(p["name"], p["high"]) for p in ranked],
        "_synth": {
            "lon_min": float(lon.min()), "lon_max": float(lon.max()),
            "lat_min": float(lat.min()), "lat_max": float(lat.max()),
            "rate": rate.tolist(),
        },
    }


# ---------- rendering ------------------------------------------------------
def print_report(r: dict) -> None:
    print(f"Prototype 2 — SEDIMENT ACCUMULATION × MHW\n{'-' * 60}")
    print(f"Grid: {r['grid_shape'][0]} × {r['grid_shape'][1]} cells, rate range "
          f"{r['rate_min']:.2f}-{r['rate_max']:.2f} mm/yr")
    print(f"Background median: {r['rate_median']:.2f} mm/yr\n")
    bs = r["basin_shares"]
    print(f"Whole-basin accumulation classes: "
          f"low={bs['low']:.2f}  med={bs['medium']:.2f}  high={bs['high']:.2f}\n")

    print(f"{'event':<9} {'name':<22} {'cells':>6} "
          f"{'low':>5} {'med':>5} {'high':>5} {'mean_mm/yr':>11}")
    print("-" * 70)
    for p in r["per_event"]:
        print(f"{p['id']:<9} {p['name']:<22} {p['cells']:6d} "
              f"{p['low']:5.2f} {p['medium']:5.2f} {p['high']:5.2f} {p['mean_rate']:11.2f}")

    print()
    print("Events ranked by high-accumulation overlap (hotspot co-location):")
    for i, (name, frac) in enumerate(r["ranked_by_high_overlap"], 1):
        bar = "█" * int(round(frac * 30))
        print(f"  {i}. {name:<22} {frac:5.2f}  {bar}")


def save_plot(r: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rate = np.array(r["_synth"]["rate"])
    extent = [r["_synth"]["lon_min"], r["_synth"]["lon_max"],
              r["_synth"]["lat_min"], r["_synth"]["lat_max"]]

    fig, ax = plt.subplots(figsize=(11, 5))
    im = ax.imshow(rate, origin="lower", extent=extent, aspect="auto",
                   cmap="YlGnBu", vmin=0, vmax=min(8.0, float(rate.max())))
    fig.colorbar(im, ax=ax, label="sedimentation rate (mm/yr)")
    for p in r["per_event"]:
        ax.scatter(p["lon"], p["lat"], s=180, facecolors="none",
                   edgecolors="crimson", linewidths=1.8)
        ax.annotate(p["id"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=8,
                    color="crimson")
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title(f"accumulation prototype — seed {r['seed']}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------- CLI ------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=2022,
                   help="RNG seed for the synthetic scenario (default: 2022).")
    p.add_argument("--step-deg", type=float, default=0.1,
                   help="Grid resolution in degrees (default: 0.1).")
    p.add_argument("--n-events", type=int, default=6,
                   help="Number of synthetic MHW events (default: 6, max 6).")
    p.add_argument("--json", metavar="PATH", type=Path,
                   help="Write the analysis as JSON to this path.")
    p.add_argument("--plot", metavar="PATH", type=Path,
                   help="Write a PNG map (requires matplotlib).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the human-readable table.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    r = analyse(args.seed, args.step_deg, args.n_events)
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
