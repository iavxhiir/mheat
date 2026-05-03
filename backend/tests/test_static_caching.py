"""Caching headers on static-doc API endpoints.

Pass 84 (the polish pass) added strong ETag + ``Cache-Control: public,
max-age=…`` to the documents that change at deploy time only — the STAC
Catalog / Collection / Item docs, every OGC API — Features landing /
conformance / collections page, the OGC API — Processes descriptor &
conformance, the overlay enumeration, the ``/api/data`` index, and
``/api/anomaly/extent``.

These tests pin that behaviour so a regression (e.g. a refactor that
forgets to wrap one endpoint in :func:`json_with_cache`) fails fast.
"""

from __future__ import annotations

import pytest

# (path, expected_max_age_lower_bound) — assertions are >= bound so we don't
# pin the exact TTL constant; that lets us tune defaults without touching
# every test.
_CACHEABLE: list[tuple[str, int]] = [
    ("/api/stac", 30),
    ("/api/stac/collections", 30),
    ("/api/ogcapi", 60),
    ("/api/ogcapi/conformance", 60),
    ("/api/ogcapi/collections", 60),
    ("/api/ogcapi/collections/aquaculture", 60),
    ("/api/overlays", 60),
    ("/api/overlays/aquaculture", 60),
    ("/api/data", 30),
    ("/api/anomaly/extent", 10),
    ("/api/processes", 60),
    ("/api/processes/conformance", 60),
    ("/api/processes/mhw-detect", 60),
]


@pytest.mark.parametrize("path,min_max_age", _CACHEABLE)
def test_static_doc_endpoints_carry_etag_and_cache_control(client, path: str, min_max_age: int):
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text[:200]}"

    etag = r.headers.get("ETag")
    assert etag, f"{path} missing ETag header"
    assert etag.startswith('"') and etag.endswith('"'), (
        f"{path} ETag must be quoted (RFC 7232): got {etag!r}"
    )

    cc = r.headers.get("Cache-Control", "")
    assert "public" in cc, f"{path} Cache-Control missing public: {cc!r}"
    assert "max-age=" in cc, f"{path} Cache-Control missing max-age: {cc!r}"
    age = int(cc.split("max-age=", 1)[1].split(",", 1)[0].strip())
    assert age >= min_max_age, f"{path} max-age={age} below floor {min_max_age}"


@pytest.mark.parametrize("path,_", _CACHEABLE)
def test_static_doc_endpoints_return_304_on_matching_if_none_match(client, path: str, _: int):
    """RFC 7232 conditional GET — same ETag → 304 with empty body."""
    r1 = client.get(path)
    etag = r1.headers["ETag"]
    r2 = client.get(path, headers={"If-None-Match": etag})
    assert r2.status_code == 304, f"{path} did not 304 on matching ETag: {r2.status_code}"
    assert r2.headers["ETag"] == etag
    assert r2.content in (b"", None), f"{path} 304 must have empty body"


def test_static_doc_etags_change_when_payload_would_change(client):
    """Two distinct OGC collections must produce distinct ETags."""
    e_aqua = client.get("/api/ogcapi/collections/aquaculture").headers["ETag"]
    e_mpa = client.get("/api/ogcapi/collections/mpa").headers["ETag"]
    assert e_aqua != e_mpa, "Different collections must yield different ETags"
