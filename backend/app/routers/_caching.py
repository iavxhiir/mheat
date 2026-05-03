"""Tiny helpers for adding ETag + Cache-Control to static-doc GETs.

The ``/api/stac/*``, ``/api/ogcapi/*``, ``/api/processes`` (descriptor /
conformance / landing), ``/api/overlays`` (kinds list), ``/api/data``
(asset index), and ``/api/anomaly/extent`` endpoints all serve documents
that change rarely — typically once per cache rebuild or per restart.
Adding a strong ``ETag`` and a short ``Cache-Control: public, max-age=...``
lets reverse proxies and browsers short-circuit the round trip without
us tracking the body in-memory.

This module is intentionally minimal — it offers two functions:

* :func:`json_with_cache` — returns a Starlette ``Response`` carrying a
  serialised JSON body plus the headers; on conditional ``If-None-Match``
  match it returns ``304`` with the same headers and an empty body.
* :func:`maybe_304` — pure ETag-only check that callers can use when they
  already render their own ``Response``.

All ETags here are **strong** (RFC 7232 §2.3): we hash the canonical body
bytes, so two responses are byte-equal iff their ETags match.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

# Default TTL for documents that change with the catalogue / cache state.
# 60 s is short enough that operators will see edits land within a minute,
# long enough that a typical browser / proxy won't re-hit the origin on
# every page open.
_DEFAULT_MAX_AGE = 60


def _etag_for_bytes(payload: bytes) -> str:
    """Return a strong, quoted RFC 7232 ETag for ``payload``."""
    return f'"{hashlib.sha256(payload).hexdigest()[:32]}"'


def json_with_cache(
    request: Request,
    payload: dict[str, Any] | list[Any],
    *,
    max_age: int = _DEFAULT_MAX_AGE,
    media_type: str = "application/json",
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """Return a JSON ``Response`` with strong ETag + ``Cache-Control``.

    The body is serialised with stable separators so the ETag is reproducible
    across processes (no whitespace drift). On a matching ``If-None-Match``
    the function returns a 304 with the same caching headers and no body.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = _etag_for_bytes(body)
    cache_control = f"public, max-age={max_age}"

    headers: dict[str, str] = {"ETag": etag, "Cache-Control": cache_control}
    if extra_headers:
        headers.update(extra_headers)

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=304, headers=headers)

    return Response(content=body, media_type=media_type, headers=headers)
