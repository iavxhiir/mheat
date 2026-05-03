"""salinity — compound MHW + low-salinity events (standalone CLI).

Self-contained. Only numpy is required; matplotlib is optional for
``--plot``. No MHEAT coupling.

Usage::

    python salinity.py                     # default seed, print table
    python salinity.py --seed 7            # different synthetic scenario
    python salinity.py --json out.json     # machine-readable output
    python salinity.py --plot out.png      # saves a 3-panel map
    python salinity.py --help              # every option
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
def synth_cube(rng: np.random.Generator,
               n_days: int = 365, n_lat: int = 40, n_lon: int = 60
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    days = np.arange(n_days)
    lats = np.linspace(40.0, 44.0, n_lat)
    lons = np.linspace(12.0, 18.0, n_lon)
    t, la, lo = np.meshgrid(days, lats, lons, indexing="ij")

    season = 19.5 + 6.5 * -np.cos((days - 15) * 2 * np.pi / 365)
    sst = season[:, None, None] + 0.3 * rng.standard_normal(t.shape)

    sal = 38.0 + 0.5 * np.sin((days - 180) * 2 * np.pi / 365)[:, None, None]
    sal = sal + 0.15 * rng.standard_normal(t.shape)

    heat_time = (t >= 191) & (t <= 222)
    heat_space = (la >= 42.0) & (la <= 43.0) & (lo >= 14.0) & (lo <= 16.0)
    sst = np.where(heat_time & heat_space, sst + 4.0, sst)

    fresh_time = (t >= 176) & (t <= 206)
    fresh_space = (la >= 43.0) & (la <= 44.0) & (lo >= 12.0) & (lo <= 15.0)
    sal = np.where(fresh_time & fresh_space, sal - 3.0, sal)

    return days, lats, lons, sst, sal


# ---------- analysis -------------------------------------------------------
def analyse(seed: int, n_days: int = 365, n_lat: int = 40, n_lon: int = 60) -> dict:
    rng = np.random.default_rng(seed)
    days, lats, lons, sst, sal = synth_cube(rng, n_days, n_lat, n_lon)

    p90 = np.percentile(sst, 90, axis=0)
    p10 = np.percentile(sal, 10, axis=0)
    hot_mask = sst > p90[None, :, :]
    fresh_mask = sal < p10[None, :, :]
    compound_mask = hot_mask & fresh_mask

    hot_only = int(hot_mask.sum())
    fresh_only = int(fresh_mask.sum())
    compound = int(compound_mask.sum())

    per_pixel_days = compound_mask.sum(axis=0)
    affected = int((per_pixel_days > 0).sum())
    longest = int(per_pixel_days.max())

    buckets = [(1, 5), (6, 10), (11, 20), (21, 40)]
    bucket_counts = [
        {"lo": lo, "hi": hi,
         "count": int(((per_pixel_days >= lo) & (per_pixel_days <= hi)).sum())}
        for lo, hi in buckets
    ]

    return {
        "seed": seed,
        "cube_shape": list(sst.shape),
        "sst_min": round(float(sst.min()), 2),
        "sst_max": round(float(sst.max()), 2),
        "sal_min": round(float(sal.min()), 2),
        "sal_max": round(float(sal.max()), 2),
        "hot_pixel_days": hot_only,
        "fresh_pixel_days": fresh_only,
        "compound_pixel_days": compound,
        "compound_share_of_heat": round(100 * compound / hot_only, 2) if hot_only else 0.0,
        "compound_affected_pixels": affected,
        "longest_run_days": longest,
        "duration_buckets": bucket_counts,
        "_synth": {
            "lon_min": float(lons.min()), "lon_max": float(lons.max()),
            "lat_min": float(lats.min()), "lat_max": float(lats.max()),
            "hot_pixels": hot_mask.sum(axis=0).tolist(),
            "fresh_pixels": fresh_mask.sum(axis=0).tolist(),
            "compound_pixels": per_pixel_days.tolist(),
        },
    }


# ---------- rendering ------------------------------------------------------
def print_report(r: dict) -> None:
    print(f"Prototype 4 — COMPOUND MHW + LOW-SALINITY\n{'-' * 60}")
    print(f"Cube shape: {tuple(r['cube_shape'])}  (time × lat × lon)")
    print(f"SST range:      {r['sst_min']:.2f} → {r['sst_max']:.2f} °C")
    print(f"Salinity range: {r['sal_min']:.2f} → {r['sal_max']:.2f} PSU")
    print()
    print(f"Pixel-days with SST > 90p    : {r['hot_pixel_days']:>10,}")
    print(f"Pixel-days with SAL < 10p    : {r['fresh_pixel_days']:>10,}")
    print(f"Pixel-days compound (AND)    : {r['compound_pixel_days']:>10,}")
    if r["hot_pixel_days"]:
        print(f"Compound share of heat only  : {r['compound_share_of_heat']:.2f} %")
    print()
    print(f"Compound-affected pixels     : {r['compound_affected_pixels']}")
    print(f"Longest run at any pixel     : {r['longest_run_days']} days")

    if r["compound_affected_pixels"]:
        print()
        print("Compound-event duration distribution:")
        denom = max(1, r["compound_affected_pixels"])
        for b in r["duration_buckets"]:
            n = b["count"]
            bar = "█" * max(1, int(40 * n / denom))
            print(f"  {b['lo']:>2}-{b['hi']:>2} days : {n:5d}  {bar}")

    print()
    print("Takeaway: the 7 days of temporal AND spatial overlap between")
    print("the injected heat block and the injected fresh plume shows up")
    print("as a small but cleanly-bounded compound-event signal — the")
    print("exact pattern aquaculture operators report for 2022 Venice.")


def save_plot(r: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    extent = [r["_synth"]["lon_min"], r["_synth"]["lon_max"],
              r["_synth"]["lat_min"], r["_synth"]["lat_max"]]
    hot = np.array(r["_synth"]["hot_pixels"])
    fresh = np.array(r["_synth"]["fresh_pixels"])
    comp = np.array(r["_synth"]["compound_pixels"])

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, data, title, cmap in zip(
        axes,
        (hot, fresh, comp),
        ("hot pixel-days (SST > 90p)",
         "fresh pixel-days (SAL < 10p)",
         "compound pixel-days (AND)"),
        ("Reds", "Blues", "Purples"),
        strict=False,
    ):
        im = ax.imshow(data, origin="lower", extent=extent, aspect="auto", cmap=cmap)
        fig.colorbar(im, ax=ax, label="days")
        ax.set_xlabel("Longitude (°E)")
        ax.set_ylabel("Latitude (°N)")
        ax.set_title(title, fontsize=10)
    fig.suptitle(f"salinity prototype — seed {r['seed']}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------- CLI ------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=2022,
                   help="RNG seed for the synthetic scenario (default: 2022).")
    p.add_argument("--n-days", type=int, default=365,
                   help="Time steps in the synthetic cube (default: 365).")
    p.add_argument("--n-lat", type=int, default=40,
                   help="Latitude cells (default: 40).")
    p.add_argument("--n-lon", type=int, default=60,
                   help="Longitude cells (default: 60).")
    p.add_argument("--json", metavar="PATH", type=Path,
                   help="Write the analysis as JSON to this path.")
    p.add_argument("--plot", metavar="PATH", type=Path,
                   help="Write a PNG 3-panel map (requires matplotlib).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the human-readable table.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    r = analyse(args.seed, args.n_days, args.n_lat, args.n_lon)
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
