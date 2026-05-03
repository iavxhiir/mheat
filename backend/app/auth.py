"""OIDC bearer-token authentication for MHEAT.

**Off by default.** When ``OIDC_ISSUER`` is set, an :class:`OidcBearerMiddleware`
validates the ``Authorization: Bearer <jwt>`` header against the OIDC
provider's JWKS (fetched from ``{issuer}/.well-known/openid-configuration``).

Protected path prefixes are configurable via ``OIDC_PROTECTED_PREFIXES``
(comma-separated; default: ``/api/processes/mhw-detect/execution``,
``/api/metrics``). Everything outside those prefixes is public — a good
default for a read-mostly STAC / OGC API service where only compute and
scrape endpoints need auth.

Design notes:

* Discovery (``/.well-known/openid-configuration``) and JWKS fetch happen
  once at startup; failures downgrade to "auth disabled" with a WARNING.
* Audience check is optional (``OIDC_AUDIENCE``). Without it we only
  verify signature + expiry + issuer.
* The validated claims land on ``request.state.user`` so downstream
  handlers can read them without re-parsing the token.

This is the same pattern the EDITO platform uses for service-to-service
auth; swapping ``OIDC_ISSUER`` to the EDITO Keycloak URL is the only
change needed post-award.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_DEFAULT_PROTECTED_PREFIXES: tuple[str, ...] = (
    "/api/processes/mhw-detect/execution",
    "/api/metrics",
)


def _protected_prefixes() -> tuple[str, ...]:
    raw = os.environ.get("OIDC_PROTECTED_PREFIXES", "")
    if not raw.strip():
        return _DEFAULT_PROTECTED_PREFIXES
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def discover_jwks_uri(issuer: str, timeout: float = 5.0) -> str | None:
    """Fetch ``{issuer}/.well-known/openid-configuration`` and return ``jwks_uri``."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json().get("jwks_uri")
    except Exception as exc:  # noqa: BLE001
        logger.warning("OIDC discovery failed at %s: %s", url, exc)
        return None


def fetch_jwks(jwks_uri: str, timeout: float = 5.0) -> dict[str, Any] | None:
    """Fetch the JWKS document."""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(jwks_uri)
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWKS fetch failed at %s: %s", jwks_uri, exc)
        return None


def _verify_jwt(
    token: str,
    jwks: dict[str, Any],
    issuer: str,
    audience: str | None,
) -> dict[str, Any]:
    """Verify a JWT against a JWKS. Raises ``jwt.PyJWTError`` on failure."""
    import json

    import jwt
    from jwt import algorithms

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    keys = {k.get("kid"): k for k in jwks.get("keys", [])}
    key_data = keys.get(kid) or next(iter(keys.values()), None)
    if key_data is None:
        raise jwt.PyJWKClientError("no JWK matches token kid")
    # ``RSAAlgorithm.from_jwk`` returns an RSA key suitable for PyJWT; the
    # upstream stub types it as ``PrivateKey | PublicKey`` which mypy can't
    # narrow here without runtime evidence, so we cast.
    public_key: Any = algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
    decoded: dict[str, Any] = jwt.decode(
        token,
        public_key,
        algorithms=[header.get("alg", "RS256")],
        issuer=issuer,
        audience=audience,
        options={"verify_aud": bool(audience)},
    )
    return decoded


class OidcBearerMiddleware(BaseHTTPMiddleware):
    """Validate ``Authorization: Bearer <jwt>`` on protected path prefixes.

    The middleware is a no-op when:
      * ``OIDC_ISSUER`` is unset, or
      * Discovery / JWKS fetch failed at startup.

    A failing token on a protected path returns a structured ``401`` via
    the app-wide error envelope (``errors.py`` owns the shape).
    """

    def __init__(
        self,
        app: Any,
        *,
        issuer: str,
        audience: str | None = None,
        jwks: dict[str, Any] | None = None,
        protected_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(app)
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._jwks = jwks
        self._prefixes = protected_prefixes or _protected_prefixes()

    def _is_protected(self, path: str) -> bool:
        return any(path.startswith(p) for p in self._prefixes)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._jwks is None or not self._is_protected(request.url.path):
            return await call_next(request)

        auth = request.headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return _unauthorized(request, "missing_bearer_token",
                                 "Authorization: Bearer <token> header required.")

        token = auth.split(" ", 1)[1].strip()
        try:
            claims = _verify_jwt(token, self._jwks, self._issuer, self._audience)
        except Exception as exc:  # noqa: BLE001
            return _unauthorized(request, "invalid_token", str(exc))

        request.state.user = claims
        return await call_next(request)


def _unauthorized(request: Request, code: str, message: str) -> JSONResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "status": 401,
            "request_id": getattr(request.state, "request_id", "-"),
        }
    }
    return JSONResponse(status_code=401, content=body, headers={"WWW-Authenticate": "Bearer"})


def configure_oidc(app: Any) -> None:
    """Wire the OIDC middleware onto ``app`` iff ``OIDC_ISSUER`` is set."""
    issuer = os.environ.get("OIDC_ISSUER", "").strip()
    if not issuer:
        return

    jwks_uri = os.environ.get("OIDC_JWKS_URI", "").strip() or discover_jwks_uri(issuer)
    if not jwks_uri:
        logger.warning("OIDC_ISSUER set but JWKS URI could not be discovered — auth disabled")
        return
    jwks = fetch_jwks(jwks_uri)
    if not jwks:
        logger.warning("OIDC JWKS fetch failed — auth disabled")
        return

    audience = os.environ.get("OIDC_AUDIENCE") or None
    app.add_middleware(
        OidcBearerMiddleware,
        issuer=issuer,
        audience=audience,
        jwks=jwks,
    )
    logger.info("OIDC auth enabled — issuer=%s prefixes=%s", issuer, _protected_prefixes())
