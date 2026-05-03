# Prototype 4/4 — compound MHW + low-salinity events

Standalone CLI program. Only `numpy` required; `matplotlib` optional for
`--plot`.

```bash
pip install numpy                    # required
pip install matplotlib               # optional, only for --plot

python salinity.py                   # default seed, print table
python salinity.py --seed 7          # different synthetic scenario
python salinity.py --n-days 180      # shorter synthetic cube
python salinity.py --json out.json   # machine-readable output
python salinity.py --plot out.png    # saves a 3-panel pixel-days map
python salinity.py --help            # every option
```

Synthesises a 365-day × 40 × 60 SST + salinity cube, injects a July
heat anomaly and an overlapping late-June fresh anomaly, and detects
pixel-days where SST > 90p AND salinity < 10p at the same time.
Reports compound footprint, duration per pixel, histogram.

Upstream in real life: Copernicus Marine
`MEDSEA_MULTIYEAR_PHY_006_004` and `MEDSEA_ANALYSISFORECAST_PHY_006_013`
(both already subset by MHEAT for SST; salinity `so` is the second
variable).

Exit code is 0 on success.
