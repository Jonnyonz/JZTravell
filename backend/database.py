import logging
import os, secrets, asyncio, uuid
from datetime import datetime, timedelta
from pathlib import Path
import asyncpg
from fastapi import HTTPException, Request, Depends
from jztech_core import sessions as core_sessions
from jztech_core.csrf import enforce_csrf, generate_csrf_token
from jztech_core.migrations import apply_migrations
from jztech_core.net import parse_networks, real_ip
from jztech_core.passwords import hash_password, needs_rehash, verify_legacy_password, verify_password

logger = logging.getLogger("jztravell")


def verify_password_any(password: str, stored_hash: str) -> bool:
    """Verifica contra Argon2id (hash actual) o un esquema legado (bcrypt), para
    poder migrar de forma transparente en el login (ver seccion 3.4 de la hoja
    de ruta: decision confirmada 2026-09-29)."""
    if stored_hash.startswith("$argon2"):
        return verify_password(password, stored_hash)
    return verify_legacy_password(password, stored_hash)

# === CONEXION A LA BASE ===
db_pool = None

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

async def init_db_schema():
    """Crea el pool de conexiones y aplica las migraciones pendientes (backend/migrations,
    jztech_core.migrations): el esquema lo crea y lo actualiza la app al arrancar.

    Reintenta la conexion (Postgres puede tardar en levantar, p. ej. en el primer
    arranque de docker-compose); si sigue sin responder despues de los reintentos,
    sigue con db_pool=None en vez de crashear el arranque completo (get_db()
    devuelve 503 mientras tanto). Esto tambien permite correr el smoke test de
    arquitectura (test_architecture.py) sin una Postgres real."""
    global db_pool
    for intento in range(10):
        try:
            db_pool = await asyncpg.create_pool(
                user=os.getenv("POSTGRES_USER", "jzadmin"), password=os.getenv("POSTGRES_PASSWORD", secrets.token_hex(24)),
                database=os.getenv("POSTGRES_DB", "jzflete_db"), host=os.getenv("POSTGRES_HOST", "db"), min_size=2, max_size=10
            )
            break
        except Exception as e:
            logger.warning("Intento %d/10 de conexion a PostgreSQL fallido: %r", intento + 1, e)
            await asyncio.sleep(1.0)

    if db_pool is None:
        logger.error("No se pudo conectar a PostgreSQL: la API respondera 503 hasta reiniciar el servicio.")
        return

    # Una migracion que falla frena el arranque (no se silencia): mejor caido que con un esquema a medias.
    aplicadas = await apply_migrations(db_pool, MIGRATIONS_DIR)
    if aplicadas:
        logger.info("Migraciones aplicadas: %s", aplicadas)

async def close_db_pool():
    if db_pool is not None:
        await db_pool.close()

async def get_db():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible.")
    async with db_pool.acquire() as connection: yield connection

# === SESIONES (jztech_core.sessions) ===
# Token opaco al azar en la cookie; en la base (jztech_sessions) solo queda su hash. Se revoca borrando la fila.
SESSION_TTL = timedelta(hours=8)
# CSRF de doble envio: la cookie csrf_token (legible por el JS) se repite en el header X-CSRF-Token en cada
# POST/PUT/DELETE. Es un HMAC con la propia sesion como clave: otro sitio no puede leerlo ni calcularlo.
CSRF_COOKIE = "csrf_token"
_CSRF_SUJETO = "jztravell-csrf"

class _ConexionComoPool:
    """jztech_core.sessions pide un pool; asi usa la conexion que ya tiene el request."""
    def __init__(self, conn):
        self._conn = conn
    def acquire(self):
        return self
    async def __aenter__(self):
        return self._conn
    async def __aexit__(self, *exc):
        return False

async def start_session(db, response, user_id, rol: str) -> None:
    await db.execute("DELETE FROM jztech_sessions WHERE expires_at <= now()")
    token = await core_sessions.create_session(_ConexionComoPool(db), str(user_id), SESSION_TTL)
    core_sessions.set_session_cookie(response, token, SESSION_TTL)
    max_age = int(SESSION_TTL.total_seconds())
    response.set_cookie(key=CSRF_COOKIE, value=generate_csrf_token(token, _CSRF_SUJETO), httponly=False,
                        secure=True, samesite="strict", max_age=max_age)
    # Solo para la UI (que pagina mostrar); la autorizacion real la hace el servidor.
    response.set_cookie(key="user_rol", value=str(rol), httponly=False, secure=True, samesite="strict", max_age=max_age)

async def end_session(db, request: Request, response) -> None:
    """Cierra solo la sesion de este dispositivo."""
    token = request.cookies.get(core_sessions.SESSION_COOKIE_NAME)
    if token:
        await core_sessions.revoke_session(_ConexionComoPool(db), token)
    core_sessions.clear_session_cookie(response)
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="strict")
    response.delete_cookie("user_rol", secure=True, samesite="strict")

async def revoke_user_sessions(db, user_id) -> None:
    """Cambio de clave o desactivacion: se cierran todas las sesiones del usuario."""
    await core_sessions.revoke_all_sessions_for_user(_ConexionComoPool(db), str(user_id))

# --- IP REAL DEL CLIENTE (DETRAS DE PROXY INVERSO) ---
# X-Forwarded-For solo se cree si la conexion viene de un proxy de confianza (ver
# jztech_core.net: por defecto loopback y redes internas de Docker, el proxy
# corre en el mismo host). Un valor invalido en TRUSTED_PROXIES frena el arranque.
TRUSTED_PROXIES = parse_networks(os.getenv("TRUSTED_PROXIES", "127.0.0.1/32,::1/128,172.16.0.0/12"))

def get_client_ip(request: Request) -> str:
    return real_ip(request, TRUSTED_PROXIES)

# Rate limit de login por IP (ademas del bloqueo por cuenta): frena el credential-stuffing
# que rota usuarios desde una misma IP.
IP_MAX_INTENTOS = 15
IP_BLOQUEO_MIN = 15

async def check_ip_rate_limit(db, ip):
    row = await db.fetchrow("SELECT bloqueado_hasta FROM login_rate_limit WHERE ip = $1", ip)
    if row and row["bloqueado_hasta"] and row["bloqueado_hasta"] > datetime.now():
        raise HTTPException(429, "Demasiados intentos desde esta red. Reintente mas tarde.")

async def record_ip_failure(db, ip):
    row = await db.fetchrow("""
        INSERT INTO login_rate_limit (ip, intentos) VALUES ($1, 1)
        ON CONFLICT (ip) DO UPDATE SET intentos = login_rate_limit.intentos + 1 RETURNING intentos
    """, ip)
    if row and row["intentos"] >= IP_MAX_INTENTOS:
        await db.execute("UPDATE login_rate_limit SET bloqueado_hasta = $1 WHERE ip = $2",
                         datetime.now() + timedelta(minutes=IP_BLOQUEO_MIN), ip)

async def reset_ip_rate_limit(db, ip):
    await db.execute("DELETE FROM login_rate_limit WHERE ip = $1", ip)

# --- CONTROL DE IDENTIDAD ---
async def get_current_user(request: Request, db: asyncpg.Connection = Depends(get_db)):
    token_sesion = request.cookies.get(core_sessions.SESSION_COOKIE_NAME)
    if not token_sesion: raise HTTPException(401, "Ausencia de credenciales.")
    user_id = await core_sessions.verify_session(_ConexionComoPool(db), token_sesion)
    try:
        uid = uuid.UUID(user_id) if user_id else None
    except ValueError:
        uid = None
    registro = await db.fetchrow("SELECT id, username, rol FROM usuarios WHERE id = $1 AND activo = TRUE", uid) if uid else None
    if not registro: raise HTTPException(401, "Sesion invalida.")
    # CSRF despues de autenticar: 403 si un POST/PUT/DELETE no trae el token de esta sesion.
    await enforce_csrf(request, token_sesion, _CSRF_SUJETO)
    return dict(registro)

def require_role(allowed_roles):
    async def role_checker(user: dict = Depends(get_current_user)):
        if user['rol'] not in allowed_roles: raise HTTPException(403, "Acceso denegado.")
        return user
    return role_checker
