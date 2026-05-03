# Prototype 3/4 — seabed substrate × MHW vulnerability

Standalone CLI program. Only `numpy` required; `matplotlib` optional for
`--plot`.

```bash
pip install numpy                     # required
pip install matplotlib                # optional, only for --plot

python substrate.py                   # default seed, print table
python substrate.py --seed 99         # different synthetic scenario
python substrate.py --step-deg 0.2    # coarser grid
python substrate.py --json out.json   # machine-readable output
python substrate.py --plot out.png    # saves a substrate map + events
python substrate.py --help            # every option
```

Synthesises a Mediterranean substrate map (rock / sand / mud / biogenic)
and 5 MHW footprints. Computes substrate-weighted vulnerability using
published ecological-mortality coefficients (biogenic 1.0, rock 0.7,
sand 0.3, mud 0.1). Ranks events — *substrate-weighted exposure is a
better ecological indicator than raw area*.

Upstream in real life: EMODnet Seabed Habitats — EUSeaMap 2023.

Exit code is 0 on success.
