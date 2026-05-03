"""erosion — coastal-erosion × MHW overlap (standalone CLI program).

Self-contained. Only numpy is required; matplotlib is optional for
``--plot``. No MHEAT coupling.

Usage::

    python erosion.py                     # default seed, print table
    python erosion.py --seed 7            # different synthetic scenario
    python erosion.py --json out.json     # machine-readable output
    python erosion.py --plot out.png      # saves a map of coast + events
    python erosion.py --help              # every option
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
def synth_events(rng: np.random.Generator, n: int = 8) -> list[dict]:
    cats = ["II Strong", "III Severe", "IV Extreme", "V Super-Extreme"]
    return [
        {
            "id": f"evt-{i:02d}",
            "lon": float(rng.uniform(0.0, 25.0)),
            "lat": float(rng.uniform(36.0, 45.0)),
            "category_name": str(rng.choice(cats)),
            "intensity_max_c": round(float(rng.uniform(1.5, 4.5)), 2),
        }
        for i in range(n)
    ]


def synth_coast(rng: np.random.Generator, n: int = 400):
    lons = rng.uniform(0.0, 25.0, n)
    lats = 36.0 + 10.0 * rng.random(n) ** 1.5
    classes = np.where(
        (lons > 12.0) & (lats > 42.0),
        rng.choice(["eroding", "stable", "accreting"], n, p=[0.55, 0.35, 0.10]),
        rng.choice(["eroding", "stable", "accreting"], n, p=[0.15, 0.70, 0.15]),
    )
    lengths_km = rng.uniform(0.5, 5.0, n)
    return lons, lats, classes, lengths_km


def dist_km(lon: float, lat: float, c_lons: np.ndarray, c_lats: np.ndarray) -> np.ndarray:
    mean_lat = np.radians(0.5 * (lat + c_lats.mean()))
    dx = (c_lons - lon) * np.cos(mean_lat) * 111.0
    dy = (c_lats - lat) * 111.0
    return np.sqrt(dx * dx + dy * dy)


# ---------- analysis -------------------------------------------------------
def analyse(seed: int, buffer_km: float = 50.0) -> dict:
    rng = np.random.default_rng(seed)
    events = synth_events(rng)
    lons, lats, classes, lengths = synth_coast(rng)

    per_event = []
    for e in events:
        d = dist_km(e["lon"], e["lat"], lons, lats)
        near = d <= buffer_km
        near_len = float(lengths[near].sum())
        erode_len = float(lengths[near & (classes == "eroding")].sum())
        frac = erode_len / near_len if near_len > 0 else 0.0
        per_event.append({**e, "near_km": round(near_len, 2),
                          "eroding_km": round(erode_len, 2),
                          "eroding_fraction": round(frac, 3)})

    total_km = float(lengths.sum())
    eroding_km = float(lengths[classes == "eroding"].sum())
    fracs = np.array([p["eroding_fraction"] for p in per_event])
    return {
        "seed": seed,
        "buffer_km": buffer_km,
        "coast_total_km": round(total_km, 1),
        "coast_eroding_km": round(eroding_km, 1),
        "coast_eroding_pct": round(100 * eroding_km / total_km, 1),
        "n_events": len(events),
        "per_event": per_event,
        "summary": {
            "mean_eroding_fraction": round(float(fracs.mean()), 3),
            "max_eroding_fraction": round(float(fracs.max()), 3),
            "events_above_30pct": int((fracs >= 0.30).sum()),
        },
        "_synth": {  # kept for the optional plot
            "coast_lons": lons.tolist(),
            "coast_lats": lats.tolist(),
            "coast_classes": classes.tolist(),
        },
    }


# ---------- rendering ------------------------------------------------------
def print_report(r: dict) -> None:
    print(f"Prototype 1 — COASTAL EROSION × MHW\n{'-' * 60}")
    print(f"Coastline: {r['coast_total_km']:.0f} km total, "
          f"{r['coast_eroding_km']:.0f} km ({r['coast_eroding_pct']:.0f} %) eroding.")
    print(f"MHW events: {r['n_events']}\n")
    print(f"{'event':<9} {'cat':<15} {'lon':>5} {'lat':>5} "
          f"{'near_km':>8} {'erode_km':>9} {'frac':>5}")
    print("-" * 60)
    for p in r["per_event"]:
        print(f"{p['id']:<9} {p['category_name']:<15} "
              f"{p['lon']:5.1f} {p['lat']:5.1f} {p['near_km']:8.1f} "
              f"{p['eroding_km']:9.1f} {p['eroding_fraction']:5.2f}")
    s = r["summary"]
    print()
    print(f"Mean eroding fraction across events : {s['mean_eroding_fraction']:.2f}")
    print(f"Max                                 : {s['max_eroding_fraction']:.2f}")
    print(f"Events with ≥ 30 % eroding coast    : {s['events_above_30pct']} / {r['n_events']}")


def save_plot(r: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    lons = np.array(r["_synth"]["coast_lons"])
    lats = np.array(r["_synth"]["coast_lats"])
    cls = np.array(r["_synth"]["coast_classes"])
    colors = {"eroding": "#e63946", "stable": "#8a9ba8", "accreting": "#2a9d8f"}
    for c, col in colors.items():
        mask = cls == c
        ax.scatter(lons[mask], lats[mask], s=12, c=col, label=f"coast ({c})", alpha=0.75)
    for p in r["per_event"]:
        ax.scatter(p["lon"], p["lat"], s=200,
                   facecolors="none", edgecolors="black", linewidths=1.5)
        ax.annotate(p["id"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=8)
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title(f"erosion prototype — seed {r['seed']}, {r['buffer_km']:.0f} km buffer")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------- CLI ------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=42,
                   help="RNG seed for the synthetic scenario (default: 42).")
    p.add_argument("--buffer-km", type=float, default=50.0,
                   help="Coastal buffer radius around each event (default: 50).")
    p.add_argument("--json", metavar="PATH", type=Path,
                   help="Write the analysis as JSON to this path.")
    p.add_argument("--plot", metavar="PATH", type=Path,
                   help="Write a PNG map (requires matplotlib).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the human-readable table.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    r = analyse(args.seed, args.buffer_km)
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
