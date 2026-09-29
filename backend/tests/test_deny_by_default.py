"""Denegar por defecto (seccion 3.3): toda ruta de la API exige sesion salvo las
listadas aca. Agregar una ruta publica nueva obliga a sumarla a esta lista a
proposito; olvidarse de proteger una ruta hace fallar el test."""

from jztech_core.deny_by_default import assert_all_routes_protected

from database import get_current_user
from main import app

PUBLIC_PATHS = [
    # Autenticacion y configuracion inicial.
    "/api/login",
    "/api/logout",
    "/api/setup/status",
    "/api/setup/admin",
    "/api/auth/google/url",
    "/api/auth/google/callback",
    # index.html la lee antes del login (nombre de empresa, si hay Google SSO).
    # No incluye google_client_secret. Ojo: la lista es por path, asi que esto
    # tambien saltea el POST /api/config (hoy protegido con require_role admin).
    "/api/config",
    # Documentacion autogenerada de FastAPI.
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
]


def test_all_routes_require_session_unless_public():
    assert_all_routes_protected(app, public_paths=PUBLIC_PATHS, auth_dependency=get_current_user)
