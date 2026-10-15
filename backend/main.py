from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from jztech_core.logging_setup import configure_logging, install_generic_error_handler
from jztech_core.security_headers import SecurityHeadersMiddleware

# Antes de importar database: ese modulo ya puede loguear al cargarse.
configure_logging()

from database import init_db_schema, close_db_pool
from routers import auth, users, config, fleet, branches, clients, shipments


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_schema()
    yield
    await close_db_pool()


app = FastAPI(title="JZ Tech Solutions - API Logística", lifespan=lifespan)
# Excepcion no manejada: traceback al log (JSON a stdout) y al cliente {"detail": "Error interno
# del servidor."} con 500 (el frontend lee "detail", el formato de FastAPI). Nunca str(e).
install_generic_error_handler(app, "jztravell", field="detail")

# === REGISTRO DE ROUTERS MODULARES ===
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(config.router)
app.include_router(fleet.router)
app.include_router(branches.router)
app.include_router(clients.router)
app.include_router(shipments.router)


# CSRF: lo valida get_current_user (database.py) en cada POST/PUT/DELETE con sesion, con un token
# ligado a la sesion (jztech_core.csrf). Login, setup y logout no tienen sesion: la cookie de sesion es
# SameSite=Strict, asi que otro sitio no puede usarla.


# Cabeceras de seguridad (jztech_core). 'unsafe-inline' es temporal: el frontend
# tiene <script>, <style> y handlers on*= inline; se quita al separar JS/CSS (seccion 3.7).
# geolocation=(self): chofer.html envia la posicion GPS del chofer.
app.add_middleware(
    SecurityHeadersMiddleware,
    csp=(
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    permissions_policy="geolocation=(self), microphone=(), camera=()",
)


app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
