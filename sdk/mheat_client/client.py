"""MheatClient — synchronous + minimal-async client for the MHEAT API.

Design goals:
- Tiny surface (one class, eight methods).
- Returns native scientific-Python types: ``dict`` for JSON, ``pandas.DataFrame``
  for tabular events, ``xarray.Dataset`` for the SST cube.
- Uses GeoParquet for ``events()`` because it's ~10x smaller + faster than
  parsing the GeoJSON polygon strings, which matters for 30-year archives.
- Opens the ARCO Zarr cube via ``xarray.open_zarr(consolidated=True)`` so a
  notebook user gets lazy, chunked, dask-friendly access.
"""

from __future__ import annotations

import io
from typing import Any

import httpx
import pandas as pd
import xarray as xr

# Re-exported for convenience but kept internal — users shouldn't need to import them.
__all__ = ["MheatClient"]

DEFAULT_TIMEOUT = 60.0


class MheatClient:
    """Synchronous client for the MHEAT REST API.

    Parameters
    ----------
    base_url:
        Origin of the MHEAT service. Defaults to ``http://localhost:8000``.
        On the EDITO Datalab this would typically be the deployed URL.
    timeout:
        Per-request timeout in seconds. Defaults to 60s — generous enough for
        wide bbox queries against the full 30-year archive.
    headers:
        Optional extra headers to include on every request (e.g. an API key).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"User-Agent": "mheat-client/0.1.0", **(headers or {})},
        )

    # ------------------------------------------------------------------ basics
    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "MheatClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"MheatClient(base_url={self.base_url!r})"

    # ----------------------------------------------------------------- helpers
    def _get_json(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        r = self._client.get(path, params=clean)
        r.raise_for_status()
        return r.json()

    # ----------------------------------------------------------------- public
    def health(self) -> dict[str, Any]:
        """Liveness probe — returns ``{"status": "ok", "version": ...}``."""
        return self._get_json("/api/health")

    def freshness(self) -> dict[str, Any]:
        """Live-data freshness snapshot — last CMS pull, age, error state."""
        return self._get_json("/api/freshness")

    def extent(self) -> dict[str, Any]:
        """Temporal + value extent of the SST anomaly cube."""
        return self._get_json("/api/anomaly/extent")

    def events(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        min_category: int = 1,
        bbox: tuple[float, float, float, float] | str | None = None,
        raw: bool = False,
        as_geodataframe: bool = False,
    ) -> pd.DataFrame:
        """Detected MHW events as a :class:`pandas.DataFrame`.

        Uses the ``/api/events.parquet`` GeoParquet endpoint — much smaller than
        GeoJSON for large archives. The ``geometry`` column contains WKB bytes;
        pass ``as_geodataframe=True`` to get a :class:`geopandas.GeoDataFrame`
        (requires the ``geo`` extra: ``pip install mheat-client[geo]``).

        Parameters
        ----------
        start, end:
            ISO date strings (``"YYYY-MM-DD"``). Defaults to whole archive.
        min_category:
            Hobday category floor (1=Moderate, 5=Super-Extreme). Defaults to 1.
        bbox:
            ``(min_lon, min_lat, max_lon, max_lat)`` tuple or ``"a,b,c,d"`` str.
        raw:
            If ``True``, request the un-clustered per-pixel events (verbose).
        as_geodataframe:
            If ``True``, decode the WKB ``geometry`` column into a GeoDataFrame.
        """
        bbox_str: str | None
        if isinstance(bbox, (tuple, list)):
            bbox_str = ",".join(str(v) for v in bbox)
        else:
            bbox_str = bbox  # type: ignore[assignment]

        r = self._client.get(
            "/api/events.parquet",
            params={k: v for k, v in {
                "start": start,
                "end": end,
                "min_category": min_category,
                "bbox": bbox_str,
                "raw": str(bool(raw)).lower() if raw else None,
            }.items() if v is not None},
        )
        r.raise_for_status()
        df = pd.read_parquet(io.BytesIO(r.content))

        # Coerce date columns to pandas datetimes for ergonomic filtering/plot.
        for col in ("date_start", "date_end", "date_peak"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        if as_geodataframe:
            try:
                import geopandas as gpd
                from shapely import wkb  # type: ignore
            except ImportError as e:  # pragma: no cover - optional dep
                raise ImportError(
                    "as_geodataframe=True requires geopandas + shapely "
                    "(pip install 'mheat-client[geo]')"
                ) from e
            df["geometry"] = df["geometry"].apply(
                lambda b: wkb.loads(b) if isinstance(b, (bytes, bytearray)) else b
            )
            return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
        return df

    def event_series(
        self,
        event_id: str,
        lon: float,
        lat: float,
        *,
        pad_days: int = 14,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """SST + climatology + threshold time-series for a pixel near an event.

        Returns a DataFrame indexed by date with columns ``sst``, ``seas``,
        ``thresh`` (and a ``variable`` attr stored on ``df.attrs['variable']``).
        Tip: pass the event's ``centroid_lon`` / ``centroid_lat`` from
        :meth:`events` for guaranteed in-polygon coverage.
        """
        payload = self._get_json(
            f"/api/events/{event_id}/series",
            lon=lon,
            lat=lat,
            pad_days=pad_days,
            start=start,
            end=end,
        )
        idx = pd.to_datetime(payload.get("times", []))
        df = pd.DataFrame(
            {
                "sst": payload.get("sst", []),
                "seas": payload.get("seas", []),
                "thresh": payload.get("thresh", []),
            },
            index=idx,
        )
        df.index.name = "time"
        df.attrs["event_id"] = payload.get("event_id", event_id)
        df.attrs["variable"] = payload.get("variable", "analysed_sst")
        df.attrs["lon"] = payload.get("lon", lon)
        df.attrs["lat"] = payload.get("lat", lat)
        return df

    def sst_cube(self, *, asset: str = "sst.zarr", consolidated: bool = True) -> xr.Dataset:
        """Open the ARCO Zarr cube as an :class:`xarray.Dataset` (lazy, chunked).

        Parameters
        ----------
        asset:
            Either ``sst.zarr`` (default — the analysed SST cube) or
            ``climatology.zarr`` (Hobday seas + thresh per DOY).
        consolidated:
            Pass through to :func:`xarray.open_zarr`. The MHEAT server publishes
            consolidated metadata so the default should work everywhere.
        """
        url = f"{self.base_url}/api/data/{asset}"
        return xr.open_zarr(url, consolidated=consolidated)

    def prefetch(self, start: str, end: str) -> dict[str, Any]:
        """Trigger an async backend pull for a date range. Returns the job state.

        Despite the name, this is a fire-and-forget HTTP POST — the backend
        kicks the pull onto its own task queue and returns immediately. Poll
        :meth:`freshness` to watch ``last_pull.in_progress``.
        """
        r = self._client.post(
            "/api/prefetch",
            params={"start": start, "end": end},
        )
        r.raise_for_status()
        # Some prefetch responses are 202 Accepted with no body.
        if r.headers.get("content-type", "").startswith("application/json") and r.content:
            return r.json()
        return {"status": "accepted", "start": start, "end": end}
