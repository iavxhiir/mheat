"""`python -m mheat` entry point.

Small CLI that makes MHEAT directly usable from an EDITO Datalab
JupyterLab terminal without having to curl the HTTP API. The CLI is a
thin adapter over the same in-process app used by the test suite.

Subcommands:

* ``events``     — fetch the MHW events GeoJSON for a bbox / time window.
* ``anomaly``    — write the anomaly PNG for a date to a local file.
* ``export-arco`` — shim over ``scripts/export_arco.py``.
* ``health``     — one-shot liveness check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _in_process_client():
    """Boot the FastAPI app in-process for one-shot CLI calls."""
    from fastapi.testclient import TestClient

    from .main import app
    return TestClient(app)


def _cmd_events(args: argparse.Namespace) -> int:
    client = _in_process_client()
    params: list[str] = []
    if args.start:
        params.append(f"start={args.start}")
    if args.end:
        params.append(f"end={args.end}")
    if args.bbox:
        params.append(f"bbox={args.bbox}")
    if args.min_category:
        params.append(f"min_category={args.min_category}")
    if args.raw:
        params.append("raw=true")
    url = "/api/events" + ("?" + "&".join(params) if params else "")
    r = client.get(url)
    if r.status_code != 200:
        print(f"{url} → {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return 2
    payload = r.json()
    out_text = json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2)
    if args.out:
        args.out.write_text(out_text, encoding="utf-8")
        print(f"wrote {args.out}  ({len(payload.get('features', []))} features)")
    else:
        sys.stdout.write(out_text)
        sys.stdout.write("\n")
    return 0


def _cmd_anomaly(args: argparse.Namespace) -> int:
    client = _in_process_client()
    r = client.get(f"/api/anomaly?date={args.date}")
    if r.status_code != 200:
        print(f"/api/anomaly?date={args.date} → {r.status_code}", file=sys.stderr)
        return 2
    args.out.write_bytes(r.content)
    print(f"wrote {args.out}  ({len(r.content):,} bytes)")
    return 0


def _cmd_export_arco(args: argparse.Namespace) -> int:
    from scripts.export_arco import main as export_main

    return export_main(args.out, args.product_version)


def _cmd_health(_: argparse.Namespace) -> int:
    client = _in_process_client()
    r = client.get("/api/health")
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    return 0 if r.status_code == 200 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mheat",
        description=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("events", help="Fetch MHW events as GeoJSON.")
    ev.add_argument("--start", help="YYYY-MM-DD inclusive start date.")
    ev.add_argument("--end", help="YYYY-MM-DD inclusive end date.")
    ev.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max.")
    ev.add_argument("--min-category", type=int, choices=range(1, 6))
    ev.add_argument("--raw", action="store_true", help="Return raw per-pixel events.")
    ev.add_argument("--out", type=Path, help="Write to file; default stdout.")
    ev.add_argument("--compact", action="store_true", help="Compact JSON.")
    ev.set_defaults(func=_cmd_events)

    an = sub.add_parser("anomaly", help="Write the anomaly PNG for a date.")
    an.add_argument("--date", required=True, help="YYYY-MM-DD.")
    an.add_argument("--out", required=True, type=Path)
    an.set_defaults(func=_cmd_anomaly)

    ar = sub.add_parser("export-arco", help="Write the MHW cube as ARCO Zarr.")
    ar.add_argument("--out", required=True, type=Path)
    ar.add_argument("--product-version", default="0.4.0")
    ar.set_defaults(func=_cmd_export_arco)

    he = sub.add_parser("health", help="One-shot liveness check.")
    he.set_defaults(func=_cmd_health)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
