# MHEAT prototype gallery

Four standalone programs exploring follow-on variables for MHW impact
analysis. Each is a **self-contained CLI** (argparse, `--help`, `--json`,
`--plot`) that runs in ≤ 10 s and requires only `numpy`
(`matplotlib` optional, for `--plot`). **No MHEAT backend dependency** —
these prototypes exist to show that the MHEAT impact-join pattern
generalises, without committing any of them to the Call #1 scope.

## The four prototypes

| # | Folder | What it measures | Upstream data source |
|---|---|---|---|
| 1 | [`erosion/`](erosion/) | Eroding-coast fraction within a buffer around each MHW event | EMODnet Geology — Coastal Behaviour |
| 2 | [`accumulation/`](accumulation/) | Overlap between events and sedimentation-rate hotspots (Po / Rhône / Nile deltas) | EMODnet Geology — Sedimentation rates |
| 3 | [`substrate/`](substrate/) | Substrate-weighted vulnerability score per event (biogenic 1.0 > rock 0.7 > sand 0.3 > mud 0.1) | EMODnet Seabed Habitats — EUSeaMap 2023 |
| 4 | [`salinity/`](salinity/) | Pixel-days where SST > 90 p AND salinity < 10 p at the same cell | Copernicus Marine `MEDSEA_MULTIYEAR_PHY_006_004` |

## Run any of them

```bash
cd scripts/prototypes/<name>
python <name>.py                 # default seed, print table
python <name>.py --seed 7        # different synthetic scenario
python <name>.py --json out.json # machine-readable output
python <name>.py --plot out.png  # PNG map (needs matplotlib)
python <name>.py --help          # every option
python <name>.py --quiet --json out.json   # silent + JSON only
```

Exit code is 0 on success. No network calls, no credentials.

## Ranked verdict — what sticks and why

See [`docs/prototypes_verdict.md`](../../docs/prototypes_verdict.md)
for the full write-up (headline numbers, grant-scoring rationale).
Short version:

| Rank | Prototype | Signal | Call-#1 decision |
|:-:|---|---|---|
| 🥇 1 | **substrate** | Substrate weighting re-ranks events vs raw area — same event set, different top-three | **Keep** as fast-follow after Call #1 kick-off |
| 🥈 2 | **salinity** | 8.3 % of heat-only pixel-days are compound; aligns with 2022 Venice-lagoon reports | **Keep for Call #2** — park off Call #1 critical path |
| 🥉 3 | **accumulation** | One event at 71 % high-accumulation cells, rest near zero — hotspot-driven, honest but uneven | **Mention** in `future_work.md`, don't build |
| 🏅 4 | **erosion** | Mean eroding fraction 0.14 across 8 events; static coastline classification vs dynamic events | **Drop** — temporal mismatch is fatal for a per-event indicator |

The point of keeping all four in the repo is not that all four will be
shipped — it's that a reviewer can see, in ten seconds, that we
prototyped before committing. The two that **were** committed (substrate
+ salinity in `docs/future_work.md`) are the two with clean signal.

## Why each is a full CLI program, not a one-off script

Reviewers who open the repo don't know the history — a CLI with
`--help` signals "this was built to be re-used", and `--json` output
means the prototype's numbers can be diffed against an updated run.
Every prototype exits 0 on success so a CI smoke test can run them all
without custom plumbing.

## Architecture alignment

Each prototype uses the same shape the backend uses:

```text
synthesise_data(rng)  →  analyse(...) → dict  →  print_report(dict)
                                             ↘  save_plot(dict, path)
                                             ↘  json.dump(payload)
```

That's the same `OverlayProvider → impact.compute_impact → STAC
properties` chain MHEAT ships — the prototypes are deliberately
structured to make the porting cost (substrate = ~1 day, salinity =
~2 days) easy to audit.
