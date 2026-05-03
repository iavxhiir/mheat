"""Smoke tests for the `python -m mheat` CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _run_cli(argv: list[str], monkeypatch, capsys) -> tuple[int, str, str]:
    """Invoke the CLI in-process with fresh env; returns (code, stdout, stderr)."""
    # Ensure repo root is on sys.path so `scripts.export_arco` imports work.
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from app.__main__ import main as cli_main

    code = cli_main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_health_is_green(monkeypatch, capsys):
    code, out, _ = _run_cli(["health"], monkeypatch, capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["version"]


def test_cli_events_writes_geojson_to_file(tmp_path, monkeypatch, capsys):
    out = tmp_path / "events.geojson"
    code, _, _ = _run_cli(
        ["events", "--start", "2022-07-01", "--end", "2022-08-15", "--out", str(out)],
        monkeypatch, capsys,
    )
    assert code == 0
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)


def test_cli_events_stdout_compact(monkeypatch, capsys):
    code, out, _ = _run_cli(
        ["events", "--start", "2022-07-01", "--end", "2022-08-15", "--compact"],
        monkeypatch, capsys,
    )
    assert code == 0
    body = json.loads(out)
    assert body["type"] == "FeatureCollection"
    # Compact output has no newline inside the JSON object (only trailing).
    assert out.count("\n") == 1


def test_cli_events_propagates_server_error_nonzero(monkeypatch, capsys):
    """Bad bbox triggers an HTTP 400; the CLI must exit non-zero."""
    code, _, err = _run_cli(
        ["events", "--bbox", "not-a-bbox"], monkeypatch, capsys,
    )
    assert code != 0
    assert "400" in err


def test_cli_anomaly_writes_png(tmp_path, monkeypatch, capsys):
    out = tmp_path / "anomaly.png"
    code, _, _ = _run_cli(
        ["anomaly", "--date", "2022-07-20", "--out", str(out)], monkeypatch, capsys,
    )
    assert code == 0
    assert out.is_file()
    # PNG magic number.
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_cli_usage_is_documented(monkeypatch, capsys):
    """`--help` on each subcommand must succeed and mention its key flags."""
    import pytest

    from app.__main__ import build_parser

    p = build_parser()
    # Top-level help prints and lists all subcommands.
    with pytest.raises(SystemExit):
        p.parse_args(["--help"])
    help_text = capsys.readouterr().out
    for cmd in ("events", "anomaly", "export-arco", "health"):
        assert cmd in help_text, f"subcommand {cmd} missing from top-level --help"
