"""End-to-end reproducibility artefact for MHEAT.

Boots the FastAPI app against an in-process ASGI client (no uvicorn socket
required), drives every public read endpoint and writes the outputs plus a
SHA-256 manifest into ``out/``.

Intended use:

* **Reviewers** — run ``python scripts/reproduce.py`` from a clean checkout
  with the baked SST + climatology Zarr stores under ``data/cache/`` and
  confirm the SHA-256 hashes match the ones in ``docs/reproducibility.md``.
* **CI** — the same script can be invoked as a job step to assert the cache
  still produces byte-identical artefacts.

Artefacts produced under ``out/``:

* ``events.geojson``              — clustered MHW polygons (demo cube).
* ``events.csv``                  — same catalog, CSV projection.
* ``event_series_sample.json``    — diagnostic time-series for one event.
* ``anomaly_2022-07-20.png``      — SST anomaly raster for the peak day.
* ``stac_collections.json``       — STAC catalog root.
* ``ogcapi_collections.json``     — OGC API Features landing.
* ``manifest.sha256``             — ``<sha256>  <filename>`` rows.

Why byte-identity matters: the demo fixture is a synthetic SST cube
(bundled under ``backend/app/fixtures/sst_med_2022_sample.nc``). Running
the Hobday 2016 detector against it with the pinned numpy / marineHeatWaves
versions should yield deterministic outputs. A hash mismatch means either
a dependency drift, a scientific-code regression, or a fixture change —
any of which should surface in review.

Climatology integrity check:
    Pass ``--include-climatology`` to additionally validate the structure
    of the pre-computed Hobday climatology zarr at
    ``$CLIMATOLOGY_STORE`` (defaults to ``/data/cache/climatology.zarr``).
    A missing artifact is treated as **OK** (demo runs ship without one);
    a present-but-malformed artifact is a hard failure (exit 3).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUT_DEFAULT = ROOT / "out"
DEFAULT_CLIMATOLOGY_PATH = ROOT / "data" / "climatology.zarr"


def _bootstrap_backend() -> None:
    """Make ``backend/`` importable before app load."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def _safe_rel(path: Path, root: Path) -> str:
    """Best-effort relative-path rendering that also tolerates paths outside the root."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_and_write(client, endpoint: str, target: Path) -> None:
    r = client.get(endpoint)
    r.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(r.content)
    rel = _safe_rel(target, ROOT)
    print(f"  wrote {rel}  ({len(r.content):,} bytes)")


# ---------------------------------------------------------------------
# Climatology verification
# ---------------------------------------------------------------------
def verify_climatology(climatology_path: Path) -> dict[str, Any]:
    """Validate the structural integrity of a pre-computed Hobday climatology zarr.

    Args:
        climatology_path: Filesystem path to the zarr store (a directory).

    Returns:
        A dict with one of two shapes:

        * ``{"present": False, "path": str, "summary": "..."}`` — the path
          does not exist. This is **not** an error in demo mode (the
          climatology is only required for live-mode endpoints), so the
          caller should treat it as a soft notice.
        * ``{"present": True, "checks": [...], "ok": bool, "summary": "...",
          "fingerprint": str | None, "path": str}`` — the zarr was opened
          and each invariant in ``checks`` was evaluated. ``ok`` is True
          iff every check's ``"ok"`` flag is True. ``fingerprint`` is the
          deterministic ``Climatology.fingerprint()`` digest if it could
          be computed, otherwise None.

    Each entry in ``checks`` is::

        {"name": str, "ok": bool, "detail": str}

    The function never prints — it returns structured data so the caller
    can render however it wants (CLI section, JSON, CI annotation, etc.).
    """
    climatology_path = Path(climatology_path)
    if not climatology_path.exists():
        return {
            "present": False,
            "path": str(climatology_path),
            "summary": (
                f"No climatology artifact at {climatology_path} — OK for demo runs "
                "(the live-mode endpoints will fall back to on-the-fly Hobday)."
            ),
        }

    # Lazy imports — only paid when the artifact actually exists.
    import numpy as np  # noqa: WPS433 (intentional local import)
    import xarray as xr  # noqa: WPS433
    from app.climatology import Climatology  # noqa: WPS433

    checks: list[dict[str, Any]] = []
    fingerprint: str | None = None

    try:
        ds = xr.open_zarr(str(climatology_path), consolidated=True)
    except Exception as exc:  # noqa: BLE001 — surface any zarr error to the reviewer
        return {
            "present": True,
            "path": str(climatology_path),
            "checks": [
                {
                    "name": "open_zarr",
                    "ok": False,
                    "detail": f"failed to open zarr: {exc!r}",
                }
            ],
            "ok": False,
            "fingerprint": None,
            "summary": f"Climatology zarr at {climatology_path} could not be opened.",
        }

    # ---- 1. schema_version ----
    schema_version = ds.attrs.get("schema_version")
    checks.append(
        {
            "name": "schema_version == '1'",
            "ok": schema_version == "1",
            "detail": f"got schema_version={schema_version!r}",
        }
    )

    # ---- 2. data variables present ----
    has_seas = "seas" in ds.data_vars
    has_thresh = "thresh" in ds.data_vars
    checks.append(
        {
            "name": "data_vars: seas and thresh present",
            "ok": has_seas and has_thresh,
            "detail": f"data_vars={list(ds.data_vars)}",
        }
    )

    # ---- 3. dayofyear length ----
    doy_len = ds.sizes.get("dayofyear")
    checks.append(
        {
            "name": "dayofyear dim length == 366",
            "ok": doy_len == 366,
            "detail": f"got dayofyear={doy_len}",
        }
    )

    # ---- 4. dtype + units per data var ----
    for var in ("seas", "thresh"):
        if var not in ds.data_vars:
            checks.append(
                {
                    "name": f"{var}: dtype float32 and units 'degC'",
                    "ok": False,
                    "detail": f"variable {var!r} missing — skipped dtype check",
                }
            )
            continue
        da = ds[var]
        dtype_ok = da.dtype == np.float32
        units = str(da.attrs.get("units", "")).strip()
        units_ok = units == "degC"
        checks.append(
            {
                "name": f"{var}: dtype float32 and units 'degC'",
                "ok": dtype_ok and units_ok,
                "detail": f"dtype={da.dtype}, units={units!r}",
            }
        )

    # ---- 5. required attrs ----
    required_attrs = (
        "clim_start",
        "clim_end",
        "pctile",
        "window_half_width",
        "smooth_width",
    )
    missing = [k for k in required_attrs if k not in ds.attrs]
    checks.append(
        {
            "name": f"required attrs present: {', '.join(required_attrs)}",
            "ok": not missing,
            "detail": "all present" if not missing else f"missing: {missing}",
        }
    )

    # ---- 6. NaN coverage per var ----
    NAN_THRESHOLD = 0.50
    for var in ("seas", "thresh"):
        if var not in ds.data_vars:
            continue
        da = ds[var]
        # Compute NaN fraction without loading the entire (potentially large)
        # array at once: xarray dispatches to dask when chunks are present.
        nan_frac = float(np.asarray(np.isnan(da).mean()))
        checks.append(
            {
                "name": f"{var}: NaN coverage < {NAN_THRESHOLD:.0%}",
                "ok": nan_frac < NAN_THRESHOLD,
                "detail": f"NaN fraction = {nan_frac:.4f}",
            }
        )

    # ---- 7. fingerprint (best-effort, never blocks ok=True) ----
    try:
        clim = Climatology(seas=ds["seas"], thresh=ds["thresh"], attrs=dict(ds.attrs))
        fingerprint = clim.fingerprint()
    except Exception as exc:  # noqa: BLE001 — informational only
        fingerprint = None
        checks.append(
            {
                "name": "fingerprint computable",
                "ok": False,
                "detail": f"fingerprint() raised: {exc!r}",
            }
        )
    else:
        checks.append(
            {
                "name": "fingerprint computable",
                "ok": True,
                "detail": fingerprint,
            }
        )

    ok = all(c["ok"] for c in checks)
    summary = (
        f"Climatology at {climatology_path}: "
        f"{sum(1 for c in checks if c['ok'])}/{len(checks)} checks passed."
    )
    return {
        "present": True,
        "path": str(climatology_path),
        "checks": checks,
        "ok": ok,
        "fingerprint": fingerprint,
        "summary": summary,
    }


def _render_climatology_section(report: dict[str, Any]) -> None:
    """Print the climatology verification report as a console section."""
    print("\n=== Climatology artifact ===")
    if not report.get("present"):
        print(f"  {report.get('summary')}")
        return
    print(f"  path: {report['path']}")
    for c in report["checks"]:
        marker = "PASS" if c["ok"] else "FAIL"
        print(f"  [{marker}] {c['name']}  ({c['detail']})")
    fp = report.get("fingerprint")
    if fp:
        print(f"  fingerprint (sha256): {fp}")
    print(f"  {report['summary']}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main(out_dir: Path, include_climatology: bool, climatology_path: Path) -> int:
    _bootstrap_backend()
    from fastapi.testclient import TestClient

    from app.main import app  # noqa: E402

    client = TestClient(app)

    health = client.get("/api/health")
    if health.status_code != 200:
        print(f"/api/health returned {health.status_code} — aborting", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing reproducibility artefacts into {out_dir}")

    targets: list[tuple[str, Path]] = [
        ("/api/events?start=2022-05-15&end=2022-09-15", out_dir / "events.geojson"),
        ("/api/events.csv?start=2022-05-15&end=2022-09-15", out_dir / "events.csv"),
        ("/api/anomaly?date=2022-07-20", out_dir / "anomaly_2022-07-20.png"),
        ("/api/stac/collections", out_dir / "stac_collections.json"),
        ("/api/ogcapi/collections", out_dir / "ogcapi_collections.json"),
    ]
    for endpoint, target in targets:
        _fetch_and_write(client, endpoint, target)

    # Drill-down series for the first event, if any.
    catalog = client.get("/api/events").json()
    features = catalog.get("features", [])
    if features:
        first = features[0]
        eid = first["id"]
        lon, lat = first["properties"]["centroid"]
        _fetch_and_write(
            client,
            f"/api/events/{eid}/series?lon={lon}&lat={lat}",
            out_dir / "event_series_sample.json",
        )

    manifest_path = out_dir / "manifest.sha256"
    lines: list[str] = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path != manifest_path:
            rel = path.relative_to(out_dir).as_posix()
            lines.append(f"{_sha256(path)}  {rel}")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote {_safe_rel(manifest_path, ROOT)}  ({len(lines)} entries)")

    print("\nSHA-256 manifest:")
    print(manifest_path.read_text(encoding="utf-8"))

    # Optional climatology integrity section. A *missing* artifact is fine
    # in demo mode; a *malformed* artifact is a hard fail.
    if include_climatology:
        report = verify_climatology(climatology_path)
        _render_climatology_section(report)
        if report.get("present") and not report.get("ok"):
            return 3

    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out",
        type=Path,
        default=OUT_DEFAULT,
        help=f"Output directory for artefacts (default: {OUT_DEFAULT}).",
    )
    p.add_argument(
        "--include-climatology",
        action="store_true",
        help=(
            "Also verify the structural integrity of the pre-computed Hobday "
            "climatology zarr (schema_version, dims, dtypes, required attrs, "
            "NaN coverage, deterministic fingerprint). A missing artifact is "
            "OK in demo mode; a malformed artifact exits non-zero."
        ),
    )
    p.add_argument(
        "--climatology-path",
        type=Path,
        default=DEFAULT_CLIMATOLOGY_PATH,
        help=(
            "Path to the climatology zarr store to verify "
            f"(default: {DEFAULT_CLIMATOLOGY_PATH}). Only consulted when "
            "--include-climatology is set."
        ),
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(
        main(
            out_dir=args.out,
            include_climatology=args.include_climatology,
            climatology_path=args.climatology_path,
        )
    )
