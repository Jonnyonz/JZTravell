import logging
import os, secrets, hashlib, asyncio
from datetime import datetime, timedelta
import asyncpg
from fastapi import HTTPException, Request, Depends
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

async def init_db_schema():
    """Crea el pool de conexiones y las tablas que la app gestiona por si sola
    (el resto del esquema lo carga init_db.sql, ver README/install.sh).

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

    # Tabla del rate limit por IP (idempotente): cubre tambien instalaciones ya inicializadas
    # cuyo init_db.sql no vuelve a ejecutarse.
    async with db_pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS login_rate_limit (ip TEXT PRIMARY KEY, intentos INT NOT NULL DEFAULT 0, bloqueado_hasta TIMESTAMP)")

async def close_db_pool():
    if db_pool is not None:
        await db_pool.close()

async def get_db():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible.")
    async with db_pool.acquire() as connection: yield connection

# El token de sesion viaja en la cookie, pero en la DB se guarda solo su hash: una lectura
# de sesiones_activas (o un backup) ya no permite robar sesiones activas.
def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()

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
    token_sesion = request.cookies.get("session_token")
    if not token_sesion: raise HTTPException(401, "Ausencia de credenciales.")
    registro = await db.fetchrow("SELECT u.id, u.username, u.rol FROM sesiones_activas s JOIN usuarios u ON s.usuario_id = u.id WHERE s.token_sesion = $1 AND s.expira_en > NOW() AND u.activo = TRUE", hash_token(token_sesion))
    if not registro: raise HTTPException(401, "Sesion invalida.")
    return dict(registro)

def require_role(allowed_roles):
    async def role_checker(user: dict = Depends(get_current_user)):
        if user['rol'] not in allowed_roles: raise HTTPException(403, "Acceso denegado.")
        return user
    return role_checker
