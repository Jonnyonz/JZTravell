"""Cabeceras de seguridad via jztech_core.security_headers. No requiere base de datos
(no se entra al lifespan: TestClient sin context manager)."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _assert_security_headers(resp):
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    csp = resp.headers["content-security-policy"]
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_frontend_has_security_headers():
    _assert_security_headers(client.get("/"))


def test_csrf_rejection_also_has_security_headers():
    # El middleware de cabeceras va por fuera del de CSRF: sus 403 tambien las llevan.
    resp = client.post("/api/login", json={"username": "x", "password": "y"})
    assert resp.status_code == 403
    _assert_security_headers(resp)


def test_geolocation_allowed_for_driver_gps():
    # chofer.html usa navigator.geolocation; si esto vuelve a "geolocation=()" se rompe el GPS.
    assert "geolocation=(self)" in client.get("/").headers["permissions-policy"]
