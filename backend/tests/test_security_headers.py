"""Tests for :class:`SecurityHeadersMiddleware`."""

from __future__ import annotations


def _get(client, path="/api/health"):
    return client.get(path)


def test_default_csp_and_hardening_headers_present(client):
    r = _get(client)
    assert r.status_code == 200
    for name in (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    ):
        assert name in r.headers, f"missing header: {name}"

    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    # CSP must at minimum forbid framing and default to self.
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_is_not_emitted_on_plain_http(client):
    """TestClient speaks http, so HSTS should be suppressed."""
    r = _get(client)
    assert "Strict-Transport-Security" not in r.headers


def test_security_headers_cover_error_responses(client):
    """A 404 must carry the same hardening headers — attackers probe 404s."""
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert "X-Content-Type-Options" in r.headers
    assert "Content-Security-Policy" in r.headers


def test_security_headers_can_be_overridden_via_env(monkeypatch):
    """CSP_POLICY env var is honoured on app start."""
    monkeypatch.setenv("CSP_POLICY", "default-src 'none'")
    from fastapi.testclient import TestClient

    from app.main import create_app  # noqa: E402

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.headers["Content-Security-Policy"] == "default-src 'none'"
