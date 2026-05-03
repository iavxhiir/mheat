"""Tests for /api/anomaly and /api/anomaly/extent."""

from __future__ import annotations


def test_anomaly_returns_png_with_etag(client):
    r = client.get("/api/anomaly?date=2022-07-20")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-type"] != ""
    etag = r.headers.get("ETag")
    assert etag
    # PNG magic number.
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_anomaly_if_none_match_returns_304(client):
    etag = client.get("/api/anomaly?date=2022-07-20").headers["ETag"]
    r = client.get("/api/anomaly?date=2022-07-20", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers["ETag"] == etag


def test_anomaly_extent_reports_full_cube_range(client):
    r = client.get("/api/anomaly/extent")
    assert r.status_code == 200
    body = r.json()
    for key in ("start", "end", "n_days", "vmin_degC", "vmax_degC"):
        assert key in body, f"missing {key}"
    assert body["n_days"] > 0
    # Start / end are ISO dates.
    assert len(body["start"]) == 10 and body["start"][4] == "-"
