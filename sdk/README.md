# mheat-client

Thin Python SDK for the [MHEAT](https://github.com/your-org/mheat) marine-heatwave HTTP API. Designed for **EDITO Datalab** users (marine biologists, climate scientists, ML engineers, API integrators) who want to pull Hobday-detected MHW events + the underlying ARCO Zarr SST cube directly into a notebook.

- One class — `MheatClient`.
- Returns native types: `dict`, `pandas.DataFrame`, `xarray.Dataset`.
- Uses **GeoParquet** for events (10x smaller than GeoJSON) and the **ARCO Zarr** cube for SST (lazy, chunked, dask-friendly).

## Install

```bash
pip install mheat-client
# Or, from a checkout of the MHEAT monorepo:
pip install -e ./sdk
# Optional extras:
pip install 'mheat-client[geo]'   # geopandas + shapely for as_geodataframe=True
pip install 'mheat-client[plot]'  # matplotlib for the tutorial notebooks
```

## Quickstart — five usage examples

### 1. Connect + check the service is alive

```python
from mheat_client import MheatClient

client = MheatClient("http://localhost:8000")
print(client.health())      # {'status': 'ok', 'version': '0.4.0'}
print(client.freshness())   # {'cube_start': '1993-01-01', 'cube_end': ..., 'last_pull': {...}}
print(client.extent())      # {'start': '1993-01-01', 'end': ..., 'n_days': 12176, ...}
```

### 2. Pull the 2022 Mediterranean summer events as a DataFrame

```python
events = client.events(
    start="2022-05-15",
    end="2022-09-15",
    min_category=3,         # Cat-III (Severe) and stronger
)
print(events.shape)          # (N, 17)
print(events.sort_values("intensity_max", ascending=False)
            .head()[["event_id", "category_name", "intensity_max",
                     "duration_days", "n_aquaculture_sites", "mpa_area_km2"]])
```

### 3. Open the ARCO SST cube + plot one day

```python
import matplotlib.pyplot as plt

cube = client.sst_cube()                          # xarray.Dataset, lazy
day = cube["analysed_sst"].sel(time="2022-08-15") # 2D slice, still lazy
day.plot(cmap="RdYlBu_r", robust=True)
plt.title("Mediterranean SST — 2022-08-15")
plt.show()
```

### 4. Plot the time series of the hottest event

```python
hottest = events.sort_values("intensity_max", ascending=False).iloc[0]
ts = client.event_series(
    event_id=hottest["event_id"],
    lon=float(hottest["centroid_lon"]),
    lat=float(hottest["centroid_lat"]),
    pad_days=14,
)

ax = ts[["sst", "seas", "thresh"]].plot(figsize=(10, 4))
ax.fill_between(ts.index, ts["thresh"], ts["sst"],
                where=ts["sst"] > ts["thresh"], color="red", alpha=0.25)
ax.set_title(f"{hottest['event_id']} — {hottest['category_name']}")
ax.set_ylabel("SST (degC)")
```

### 5. Decode the polygon column into a GeoDataFrame

```python
gdf = client.events(
    start="2022-05-15",
    end="2022-09-15",
    min_category=1,
    as_geodataframe=True,    # requires geopandas + shapely
)
gdf.plot(column="category", cmap="YlOrRd", legend=True)
```

## Methods reference

| Method | Returns | Endpoint |
|---|---|---|
| `health()` | `dict` | `GET /api/health` |
| `freshness()` | `dict` | `GET /api/freshness` |
| `extent()` | `dict` | `GET /api/anomaly/extent` |
| `events(start, end, min_category, bbox, as_geodataframe)` | `pd.DataFrame` (or `gpd.GeoDataFrame`) | `GET /api/events.parquet` |
| `event_series(event_id, lon, lat, pad_days)` | `pd.DataFrame` | `GET /api/events/{id}/series` |
| `sst_cube(asset)` | `xarray.Dataset` | Zarr at `/api/data/sst.zarr` |
| `prefetch(start, end)` | `dict` | `POST /api/prefetch` |

## Use as a context manager

```python
with MheatClient("https://mhw.edito.example.com") as client:
    df = client.events(start="2024-01-01", end="2024-12-31", min_category=2)
```

## Publishing

This SDK is **not yet on PyPI**. Maintainers can publish a release with:

```bash
# 1. From sdk/ directory, bump the version in pyproject.toml + mheat_client/__init__.py.
# 2. Build the sdist + wheel:
python -m build

# 3. (Recommended) test the upload on TestPyPI first:
twine upload --repository testpypi dist/*

# 4. Then publish to real PyPI:
twine upload dist/*

# 5. Tag the release:
git tag -a sdk-v0.1.0 -m "mheat-client 0.1.0"
git push origin sdk-v0.1.0
```

You'll need API tokens for PyPI / TestPyPI in `~/.pypirc`:

```ini
[testpypi]
username = __token__
password = pypi-<your-test-token>

[pypi]
username = __token__
password = pypi-<your-prod-token>
```

After publish, install with `pip install mheat-client` from anywhere.

## License

MIT — see the parent repository's `LICENSE` file.
