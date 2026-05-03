# MHEAT tutorials

End-to-end walkthroughs of the MHEAT Mediterranean marine-heatwave workflow,
designed to run **without a Copernicus account** against the bundled synthetic
SST cube. Every code cell is identical across languages — only the narrative
text is translated, so you can switch between versions at any time.

| Language | File | EDITO locale |
|---|---|---|
| English (source) | [`mhw_mediterranean.ipynb`](mhw_mediterranean.ipynb) | `en` |
| Français | [`mhw_mediterranean_fr.ipynb`](mhw_mediterranean_fr.ipynb) | `fr` |
| Italiano | [`mhw_mediterranean_it.ipynb`](mhw_mediterranean_it.ipynb) | `it` |

Each notebook covers:

1. Loading a Mediterranean SST cube (synthetic fixture or Copernicus live)
2. Hobday 2016 detection on a single pixel + diagnostic plot
3. Pixel-wise detection + space-time clustering
4. Spatial event-density map + intensity histogram
5. Joining events with aquaculture, MPA and seagrass overlays

## Running on EDITO Datalab

```bash
# from a JupyterLab terminal on Datalab
git clone https://github.com/<your-org>/mheat.git
cd mheat
pip install -r backend/requirements.txt
jupyter lab tutorials/
```

Open any of the three notebooks and run all cells. For live Copernicus data,
set `COPERNICUSMARINE_SERVICE_USERNAME` / `COPERNICUSMARINE_SERVICE_PASSWORD`
before launching JupyterLab.

## Regenerating the translations

The French and Italian markdown is maintained as a table in
`_generate_translations.py`. If you edit the English notebook:

```bash
python tutorials/_generate_translations.py
```

This rewrites both `_fr.ipynb` and `_it.ipynb` preserving every code cell
verbatim.
