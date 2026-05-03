"""Uniform JSON error envelope for MHEAT.

Every non-2xx response from the API carries the shape::

    {
      "error": {
        "code":       "<machine-readable slug>",
        "message":    "<human-readable description>",
        "status":     <HTTP status code>,
        "request_id": "<propagated X-Request-Id>"
      }
    }

This is a strict superset of what FastAPI would return on its own — clients
that already understand ``{"detail": "..."}`` can still parse the
``message`` field. The wrapper is applied by installing three FastAPI
exception handlers in :func:`register_error_handlers`.

Stable error codes are defined in :data:`_DETAIL_CODE_MAP` — a detail
string registered there always maps to the same code, so client code can
switch on ``error.code`` instead of pattern-matching the human message.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

# Stable code mapping for detail strings produced by MHEAT routers.
# Keys are case-insensitive substrings; first match wins.
_DETAIL_CODE_MAP: list[tuple[str, str]] = [
    ("metrics disabled", "metrics_disabled"),
    ("bbox must be", "bbox_invalid"),
    ("invalid datetime", "datetime_invalid"),
    ("collection not found", "collection_not_found"),
    ("feature not found", "feature_not_found"),
    ("point out of range", "point_out_of_range"),
    ("no sst variable", "sst_variable_missing"),
    ("unknown overlay kind", "overlay_kind_unknown"),
    ("copernicus marine credentials", "cms_credentials_missing"),
]

# Stable codes for *dict-detail* HTTPExceptions (carrying a ``status`` slug).
# A dict like ``{"status": "climatology_missing", ...}`` becomes
# ``error.code == "climatology_missing"`` instead of the generic
# ``service_unavailable`` so clients can switch on the slug directly.
_STATUS_SLUG_CODES: frozenset[str] = frozenset({
    "climatology_missing",
    "cms_credentials_missing",
    "cms_unavailable",
    "sst_cache_missing",
    "dates_required",
})


def _code_for_status(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        503: "service_unavailable",
    }.get(status, "error")


def _code_from_detail(detail: Any, fallback_status: int) -> str:
    """Pick a stable code from the detail payload, falling back to the status.

    Three lookup paths, first match wins:

    1. ``detail`` is a dict carrying a known ``status`` slug
       (e.g. ``{"status": "climatology_missing", ...}``) → use that slug.
       This is the canonical 503-with-remediation shape used across MHEAT.
    2. ``detail`` is a string containing one of the configured needles
       (e.g. ``"bbox must be ..."``) → :data:`_DETAIL_CODE_MAP`.
    3. Fallback to the generic per-status code (``not_found``, ``service_unavailable``).
    """
    if isinstance(detail, dict):
        slug = detail.get("status")
        if isinstance(slug, str) and slug in _STATUS_SLUG_CODES:
            return slug
    if isinstance(detail, str):
        lower = detail.lower()
        for needle, code in _DETAIL_CODE_MAP:
            if needle in lower:
                return code
    return _code_for_status(fallback_status)


def _envelope(
    *,
    status: int,
    message: str,
    code: str,
    request: Request,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "status": status,
            "request_id": getattr(request.state, "request_id", "-"),
        }
    }
    if extra:
        body["error"].update(extra)
    return JSONResponse(status_code=status, content=body)


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # FastAPI's HTTPException.detail is typed str upstream but users may pass
    # dicts / lists; widen the view locally so isinstance checks aren't unreachable.
    detail: Any = exc.detail
    if isinstance(detail, (dict, list)):
        message = "Request failed."
        extra: dict[str, Any] = {"detail": detail}
    else:
        message = str(detail) if detail is not None else "Request failed."
        extra = {}
    return _envelope(
        status=exc.status_code,
        message=message,
        code=_code_from_detail(detail, exc.status_code),
        request=request,
        extra=extra or None,
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _envelope(
        status=422,
        message="Request payload failed validation.",
        code="validation_error",
        request=request,
        extra={"errors": exc.errors()},
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never leak the stack trace to the client — the access-log middleware
    # has already recorded the full context server-side.
    return _envelope(
        status=500,
        message="Unexpected server error.",
        code="internal_error",
        request=request,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the three handlers on a FastAPI app."""
    # FastAPI's add_exception_handler signature uses a broad Callable; our
    # typed handlers are subtype-compatible at runtime. The cast keeps mypy
    # happy without widening the handler signatures themselves.
    app.add_exception_handler(HTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_exception_handler)
