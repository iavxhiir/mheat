"""Standalone validator for the MHEAT pre-computed Hobday climatology zarr.

Pass/fail quality report covering schema (366*lat*lon, vars ``seas`` + ``thresh``
in degC, required attrs from app.climatology), data integrity (NaN, ranges,
thresh>=seas, DOY continuity), and provenance.

Usage::

    python scripts/validate_climatology.py [--path PATH] [--strict] [--json]

Exit codes: 0 = PASS, 1 = WARN-as-fail (only with --strict), 2 = ERROR.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

# backend/ on sys.path so we can pull Climatology + the canonical schema
# constant — without making the validator depend on FastAPI.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))
try:
    from app.climatology import CLIMATOLOGY_SCHEMA_VERSION, DOY_LEN, Climatology
except Exception:  # noqa: BLE001 — the validator must still run.
    CLIMATOLOGY_SCHEMA_VERSION, DOY_LEN, Climatology = "1", 366, None  # type: ignore[assignment,misc]

log = logging.getLogger("validate_climatology")
OK, WARN, ERROR = "ok", "warn", "error"
REQUIRED_ATTRS = ("schema_version", "clim_start", "clim_end", "pctile",
                  "window_half_width", "smooth_width", "source_dataset", "created_utc")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate_climatology",
        description="Validate an MHEAT climatology.zarr artifact.",
    )
    p.add_argument("--path", type=Path,
                   default=Path(os.environ.get("CLIMATOLOGY_STORE", "data/climatology.zarr")),
                   help="Path to climatology zarr (default: data/climatology.zarr or $CLIMATOLOGY_STORE).")
    p.add_argument("--strict", action="store_true", help="Exit non-zero on any warning.")
    p.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    p.add_argument("--log-level", default="INFO")
    return p


def _add(checks: list, name: str, level: str, detail: str) -> None:
    checks.append({"name": name, "level": level, "detail": detail})


def _check_layout(checks: list, path: Path) -> bool:
    """1-3: directory-shaped, zarr-flagged. Permissive: zarr can live behind
    junctions/symlinks, so ``Path.exists`` is the durable signal."""
    if not path.exists():
        _add(checks, "Path exists", ERROR, f"no such path: {path}")
        return False
    if not path.is_dir():
        _add(checks, "Path is directory", ERROR, f"not a directory: {path}")
        return False
    _add(checks, "Path exists and is a directory", OK, str(path))
    has_zg, has_zm = (path / ".zgroup").exists(), (path / ".zmetadata").exists()
    if not (has_zg or has_zm):
        _add(checks, "Zarr group sentinel", ERROR,
             "neither .zgroup nor .zmetadata present — not a zarr group")
        return False
    _add(checks, "Zarr group sentinel", OK,
         f"found {'.zmetadata (consolidated)' if has_zm else '.zgroup'}")
    return True


def _open_dataset(checks: list, path: Path) -> xr.Dataset | None:
    try:
        ds = xr.open_zarr(str(path), consolidated=True)
        _add(checks, "Open zarr", OK, "xr.open_zarr() succeeded")
        return ds
    except Exception as e:  # noqa: BLE001
        try:
            ds = xr.open_zarr(str(path), consolidated=False)
            _add(checks, "Open zarr", WARN, f"opened without consolidated metadata: {e}")
            return ds
        except Exception as e2:  # noqa: BLE001
            _add(checks, "Open zarr", ERROR, f"xr.open_zarr() failed: {e2}")
            return None


def _check_schema(checks: list, ds: xr.Dataset) -> None:
    """4-8: vars, dim length, dtypes/units, attrs, schema_version."""
    missing_vars = [v for v in ("seas", "thresh") if v not in ds.data_vars]
    if missing_vars:
        _add(checks, "Required variables present", ERROR, f"missing: {', '.join(missing_vars)}")
        return
    _add(checks, "Required variables present", OK, "seas, thresh")
    if "dayofyear" not in ds.dims:
        _add(checks, "dayofyear dim", ERROR, "dim 'dayofyear' absent")
        return
    n = int(ds.sizes["dayofyear"])
    _add(checks, "dayofyear length", OK if n == DOY_LEN else ERROR,
         f"{n}" if n == DOY_LEN else f"got {n}, expected {DOY_LEN}")
    for v in ("seas", "thresh"):
        da = ds[v]
        _add(checks, f"{v} dtype", OK if str(da.dtype) == "float32" else ERROR,
             "float32" if str(da.dtype) == "float32" else f"{da.dtype} (expected float32)")
        u = da.attrs.get("units", "")
        _add(checks, f"{v} units", OK if u == "degC" else WARN,
             "degC" if u == "degC" else f"{u!r} (expected 'degC')")
    missing = [a for a in REQUIRED_ATTRS if a not in ds.attrs]
    _add(checks, "Required attrs present",
         ERROR if missing else OK,
         f"missing: {', '.join(missing)}" if missing else ", ".join(REQUIRED_ATTRS))
    sv = str(ds.attrs.get("schema_version", ""))
    if not sv:
        _add(checks, "Schema version", ERROR, "schema_version attr is empty")
    elif sv != CLIMATOLOGY_SCHEMA_VERSION:
        try:
            if int(sv) > int(CLIMATOLOGY_SCHEMA_VERSION):
                _add(checks, "Schema version", ERROR,
                     f"artifact schema_version={sv} is newer than this validator "
                     f"({CLIMATOLOGY_SCHEMA_VERSION}); upgrade the validator")
            else:
                _add(checks, "Schema version", ERROR,
                     f"unsupported schema_version={sv} (expected {CLIMATOLOGY_SCHEMA_VERSION})")
        except ValueError:
            _add(checks, "Schema version", ERROR, f"schema_version={sv!r} not parseable as int")
    else:
        _add(checks, "Schema version", OK, sv)


def _check_data(checks: list, ds: xr.Dataset) -> dict[str, Any]:
    """9-12: NaNs, ranges, thresh>=seas, DOY continuity."""
    summary: dict[str, Any] = {}
    if "seas" not in ds.data_vars or "thresh" not in ds.data_vars:
        return summary
    seas, thresh = ds["seas"].values, ds["thresh"].values

    # 9. NaN coverage. Land + bad regions are common; warn at >30%, error at >90%.
    for name, arr in (("seas", seas), ("thresh", thresh)):
        pct = float(np.isnan(arr).mean() * 100.0)
        summary[f"nan_pct_{name}"] = round(pct, 2)
        if pct > 90.0:
            _add(checks, f"NaN coverage {name}", ERROR,
                 f"{pct:.1f}% NaN — almost-all-NaN, likely broken artifact")
        elif pct > 30.0:
            _add(checks, f"NaN coverage {name}", WARN,
                 f"{pct:.1f}% NaN (above 30% — verify land mask)")
        else:
            _add(checks, f"NaN coverage {name}", OK,
                 f"{pct:.1f}% (within expected land-mask range)")

    # 10. Plausible degC ranges (warn outside expected, error far outside).
    for name, arr, lo, hi, far_lo, far_hi in (
        ("seas", seas, -5.0, 40.0, -20.0, 60.0),
        ("thresh", thresh, -3.0, 45.0, -20.0, 60.0),
    ):
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            _add(checks, f"{name} range", ERROR, "no finite values to range-check")
            continue
        amin, amax = float(finite.min()), float(finite.max())
        summary[f"{name}_min"], summary[f"{name}_max"] = round(amin, 2), round(amax, 2)
        msg = f"[{amin:.1f}, {amax:.1f}] degC"
        if amin < far_lo or amax > far_hi:
            _add(checks, f"{name} range", ERROR,
                 f"{msg} — far outside plausible [{far_lo}, {far_hi}]")
        elif amin < lo or amax > hi:
            _add(checks, f"{name} range", WARN, f"{msg} — outside expected [{lo}, {hi}]")
        else:
            _add(checks, f"{name} range", OK, msg)

    # 11. thresh >= seas almost everywhere (90th pctile is by construction >= mean).
    both = np.isfinite(seas) & np.isfinite(thresh)
    if both.any():
        viol = float(((thresh < seas) & both).sum() / both.sum() * 100.0)
        summary["thresh_lt_seas_pct"] = round(viol, 2)
        if viol > 25.0:
            _add(checks, "thresh >= seas", ERROR,
                 f"{viol:.2f}% of cells violate (>25% — broken percentile)")
        elif viol > 5.0:
            _add(checks, "thresh >= seas", WARN,
                 f"{viol:.2f}% of cells violate (>5% — investigate)")
        else:
            _add(checks, "thresh >= seas", OK, f"holds in {100.0 - viol:.2f}% of cells")
    else:
        _add(checks, "thresh >= seas", WARN, "no overlapping finite cells")

    # 12. DOY continuity p99 — large jumps after 31-day smooth indicate broken smoothing.
    if seas.ndim == 3 and seas.shape[0] >= 2:
        with np.errstate(invalid="ignore"):
            mj = np.nanmax(np.abs(np.diff(seas, axis=0)), axis=0)
        f = mj[np.isfinite(mj)]
        if f.size:
            p99 = float(np.percentile(f, 99))
            summary["doy_jump_p99"] = round(p99, 3)
            if p99 >= 1.0:
                _add(checks, "DOY continuity (p99 max-jump)", ERROR,
                     f"{p99:.2f} degC — likely broken smoothing")
            elif p99 >= 0.5:
                _add(checks, "DOY continuity (p99 max-jump)", WARN,
                     f"{p99:.2f} degC (>= 0.5 threshold)")
            else:
                _add(checks, "DOY continuity (p99 max-jump)", OK, f"{p99:.3f} degC")
        else:
            _add(checks, "DOY continuity (p99 max-jump)", WARN, "no finite jumps to summarise")
    return summary


def _check_provenance(checks: list, ds: xr.Dataset) -> None:
    """13-15: clim_start/end ordering & span, created_utc ISO, bbox sanity."""
    cs, ce = ds.attrs.get("clim_start"), ds.attrs.get("clim_end")
    if cs is not None and ce is not None:
        try:
            i, j = int(cs), int(ce)
            if i >= j:
                _add(checks, "clim_start < clim_end", ERROR, f"clim_start={i} >= clim_end={j}")
            elif (j - i + 1) < 10:
                _add(checks, "Climatology span >= 10 years", WARN, f"span={j - i + 1} years")
            else:
                _add(checks, "Climatology span", OK, f"{i}-{j} ({j - i + 1} years)")
        except (TypeError, ValueError):
            _add(checks, "clim_start/clim_end", ERROR,
                 f"non-integer years: clim_start={cs!r}, clim_end={ce!r}")
    created = ds.attrs.get("created_utc")
    if created:
        # datetime.fromisoformat doesn't speak trailing "Z" until 3.11; smooth it.
        try:
            datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            _add(checks, "created_utc ISO-8601", OK, str(created))
        except ValueError as e:
            _add(checks, "created_utc ISO-8601", ERROR, f"unparseable: {created!r} ({e})")
    bbox = ds.attrs.get("bbox")
    if bbox is not None:
        try:
            parts = [float(x) for x in list(bbox)]
        except (TypeError, ValueError):
            _add(checks, "bbox", ERROR, f"not a 4-float list: {bbox!r}")
            return
        if len(parts) != 4:
            _add(checks, "bbox", ERROR, f"expected 4 floats, got {len(parts)}")
            return
        x0, y0, x1, y1 = parts
        if not (-180 <= x0 < x1 <= 180 and -90 <= y0 < y1 <= 90):
            _add(checks, "bbox", ERROR, f"out-of-range or degenerate: {parts}")
        else:
            _add(checks, "bbox", OK, f"{parts}")


def _summary_metadata(ds: xr.Dataset, path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "schema_version": str(ds.attrs.get("schema_version", "")),
        "source": str(ds.attrs.get("source_dataset", "")),
        "clim_start": ds.attrs.get("clim_start"),
        "clim_end": ds.attrs.get("clim_end"),
        "created_utc": str(ds.attrs.get("created_utc", "")),
        "bbox": list(ds.attrs.get("bbox", [])) or None,
        "shape": list(ds["seas"].shape) if "seas" in ds.data_vars else None,
    }
    if Climatology is not None:
        # Optional: fingerprint via Climatology.open. Gracefully skip if unavailable.
        try:
            info["fingerprint"] = Climatology.open(path).fingerprint()
        except (AttributeError, Exception):  # noqa: BLE001
            info["fingerprint"] = None
    return info


def _render_text(info: dict, checks: list, result: str, n_warn: int, n_err: int) -> str:
    out = [f"Climatology Validator - schema_version={info.get('schema_version') or '?'}",
           "=" * 40, f"Path:         {info.get('path')}"]
    if info.get("source"):
        out.append(f"Source:       {info['source']}")
    cs, ce = info.get("clim_start"), info.get("clim_end")
    if cs is not None and ce is not None:
        try:
            out.append(f"Period:       {cs}-{ce} ({int(ce) - int(cs) + 1} years)")
        except (TypeError, ValueError):
            out.append(f"Period:       {cs} to {ce}")
    if info.get("shape"):
        out.append(f"Grid:         {tuple(info['shape'])}")
    if info.get("bbox"):
        out.append(f"Bbox:         {info['bbox']}")
    if info.get("created_utc"):
        out.append(f"Created:      {info['created_utc']}")
    fp = info.get("fingerprint")
    if fp:
        out.append(f"Fingerprint:  {fp[:8]}...{fp[-4:]}" if len(fp) >= 16 else f"Fingerprint:  {fp}")
    out.extend(["", "Checks:"])
    glyph = {OK: "[ok]", WARN: "[warn]", ERROR: "[FAIL]"}
    for c in checks:
        out.append(f"  {glyph[c['level']]} {c['name']}: {c['detail']}")
    suffix = []
    if n_warn:
        suffix.append(f"{n_warn} warning{'s' if n_warn != 1 else ''}")
    if n_err:
        suffix.append(f"{n_err} error{'s' if n_err != 1 else ''}")
    out.extend(["", f"Result: {result}{f' ({chr(44).join(suffix)})' if suffix else ''}"])
    return "\n".join(out)


def _render_json(info: dict, checks: list, summary: dict, result: str,
                 n_warn: int, n_err: int) -> str:
    return json.dumps({
        "path": info.get("path"),
        "schema_version": info.get("schema_version"),
        "result": result, "warnings": n_warn, "errors": n_err, "checks": checks,
        "summary": {**summary, **{k: v for k, v in info.items()
                                  if k in ("source", "clim_start", "clim_end",
                                           "created_utc", "bbox", "shape", "fingerprint")}},
    }, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    checks: list = []
    summary: dict[str, Any] = {}
    info: dict[str, Any] = {"path": str(args.path), "schema_version": "?"}
    if _check_layout(checks, args.path):
        ds = _open_dataset(checks, args.path)
        if ds is not None:
            _check_schema(checks, ds)
            summary = _check_data(checks, ds)
            _check_provenance(checks, ds)
            info = _summary_metadata(ds, args.path)
            try:
                ds.close()
            except Exception:  # noqa: BLE001
                pass
    n_err = sum(1 for c in checks if c["level"] == ERROR)
    n_warn = sum(1 for c in checks if c["level"] == WARN)
    result = "FAIL" if n_err else ("FAIL (strict)" if n_warn and args.strict else "PASS")
    print(_render_json(info, checks, summary, result, n_warn, n_err) if args.json
          else _render_text(info, checks, result, n_warn, n_err))
    return 2 if n_err else (1 if n_warn and args.strict else 0)


if __name__ == "__main__":
    sys.exit(main())
