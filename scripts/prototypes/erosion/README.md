# Prototype 1/4 — coastal-erosion × MHW

Standalone CLI program (no MHEAT dependency). Only `numpy` required;
`matplotlib` is optional for `--plot`.

```bash
pip install numpy                   # required
pip install matplotlib              # optional, only for --plot

python erosion.py                   # default seed, print table
python erosion.py --seed 7          # different synthetic scenario
python erosion.py --buffer-km 30    # tighter coastal buffer
python erosion.py --json out.json   # machine-readable output
python erosion.py --plot out.png    # saves a map of coast + events
python erosion.py --help            # every option
python erosion.py --quiet --json out.json   # silent + JSON only
```

Synthesises 8 MHW events + 400 EMODnet-style coastal segments
{eroding / stable / accreting}, joins on a 50 km buffer, reports the
eroding fraction per event.

Upstream in real life: EMODnet Geology, layer `emodnet:coastal_behaviour`
(CC-BY-4.0).

Exit code is 0 on success.
