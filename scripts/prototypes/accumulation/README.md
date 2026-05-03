# Prototype 2/4 — sediment accumulation × MHW

Standalone CLI program. Only `numpy` required; `matplotlib` optional for
`--plot`.

```bash
pip install numpy                        # required
pip install matplotlib                   # optional, only for --plot

python accumulation.py                   # default seed, print table
python accumulation.py --seed 7          # different synthetic scenario
python accumulation.py --step-deg 0.2    # coarser grid
python accumulation.py --json out.json   # machine-readable output
python accumulation.py --plot out.png    # saves a map of hotspots + events
python accumulation.py --help            # every option
```

Builds a synthetic Mediterranean sedimentation-rate grid (three Gaussian
hotspots over the Po / Rhône / Nile deltas) and overlays six MHW
footprints. Reports per-event cell count, class distribution
(low / medium / high), area-weighted mean rate, and a
high-accumulation-overlap ranking.

Upstream in real life: EMODnet Geology sedimentation-rate product.

Exit code is 0 on success.
