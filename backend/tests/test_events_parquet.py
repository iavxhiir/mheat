"""Tests for the /api/events.parquet GeoParquet export."""

from __future__ import annotations

import io


def _parquet_table(body: bytes):
    import pyarrow.parquet as pq
    return pq.read_table(io.BytesIO(body))


def test_events_parquet_returns_a_parquet_binary(client):
    r = client.get("/api/events.parquet?start=2022-07-01&end=2022-08-15")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.apache.parquet"
    assert "attachment" in r.headers.get("content-disposition", "")
    # Parquet magic bytes at head + tail.
    body = r.content
    assert body[:4] == b"PAR1"
    assert body[-4:] == b"PAR1"


def test_events_parquet_row_count_matches_geojson(client):
    geojson = client.get("/api/events?start=2022-07-01&end=2022-08-15").json()
    n_features = len(geojson.get("features", []))
    parquet = client.get("/api/events.parquet?start=2022-07-01&end=2022-08-15").content
    table = _parquet_table(parquet)
    assert table.num_rows == n_features


def test_events_parquet_includes_hobday_columns_and_impact(client):
    parquet = client.get("/api/events.parquet?start=2022-07-01&end=2022-08-15").content
    table = _parquet_table(parquet)
    names = set(table.column_names)
    # Core Hobday fields.
    for col in (
        "event_id", "date_start", "date_end", "date_peak", "duration_days",
        "intensity_max", "intensity_mean", "intensity_cumulative",
        "category", "category_name",
        "n_aquaculture_sites", "mpa_area_km2", "seagrass_area_km2",
        "centroid_lon", "centroid_lat", "geometry",
    ):
        assert col in names, f"GeoParquet missing column: {col}"


def test_events_parquet_has_geoparquet_metadata(client):
    """GeoParquet v1.0 stores a `geo` key in the footer metadata describing the geometry column."""
    parquet = client.get("/api/events.parquet?start=2022-07-01&end=2022-08-15").content
    table = _parquet_table(parquet)
    meta = table.schema.metadata or {}
    keys = {k.decode() if isinstance(k, bytes) else k for k in meta.keys()}
    assert "geo" in keys, f"GeoParquet `geo` metadata missing; found {keys}"


def test_events_parquet_respects_min_category(client):
    baseline = _parquet_table(
        client.get("/api/events.parquet?start=2022-07-01&end=2022-08-15").content,
    ).num_rows
    strict = _parquet_table(
        client.get(
            "/api/events.parquet?start=2022-07-01&end=2022-08-15&min_category=4",
        ).content,
    ).num_rows
    assert strict <= baseline


def test_events_parquet_roundtrips_via_geopandas(client):
    import geopandas as gpd

    blob = client.get("/api/events.parquet?start=2022-07-01&end=2022-08-15").content
    gdf = gpd.read_parquet(io.BytesIO(blob))
    # CRS should be WGS84 (EPSG:4326 / CRS84).
    assert gdf.crs is not None
    assert "4326" in str(gdf.crs) or "CRS84" in str(gdf.crs)
    # Every row has a valid geometry.
    assert gdf.geometry.notna().all()
