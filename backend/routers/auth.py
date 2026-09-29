import asyncio, os, re, secrets, httpx
from datetime import datetime, timedelta
from urllib.parse import urlencode
import bcrypt
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import asyncpg

from database import get_db, hash_token, get_client_ip, check_ip_rate_limit, record_ip_failure, reset_ip_rate_limit

router = APIRouter(prefix="/api", tags=["Auth"])

SETUP_TOKEN = os.getenv("SETUP_TOKEN", "")


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupAdminRequest(BaseModel):
    token: str
    username: str
    full_name: str
    password: str


@router.post("/login")
async def login(data: LoginRequest, request: Request, response: Response, db: asyncpg.Connection = Depends(get_db)):
    ip = get_client_ip(request)
    await check_ip_rate_limit(db, ip)
    user = await db.fetchrow("SELECT id, password_hash, rol, intentos_fallidos, bloqueado_hasta FROM usuarios WHERE username = $1 AND activo = TRUE", data.username)
    if not user:
        await record_ip_failure(db, ip)
        raise HTTPException(401, "Credenciales incorrectas.")
    if user['bloqueado_hasta'] and user['bloqueado_hasta'] > datetime.now(): raise HTTPException(423, "Cuenta bloqueada temporalmente.")
    if not await asyncio.to_thread(bcrypt.checkpw, data.password.encode(), user['password_hash'].encode()):
        await record_ip_failure(db, ip)
        intentos = user['intentos_fallidos'] + 1
        if intentos >= 5:
            await db.execute("UPDATE usuarios SET intentos_fallidos = $1, bloqueado_hasta = $2 WHERE id = $3", intentos, datetime.now() + timedelta(minutes=15), user['id'])
            raise HTTPException(423, "Cuenta bloqueada por 15 minutos.")
        await db.execute("UPDATE usuarios SET intentos_fallidos = $1 WHERE id = $2", intentos, user['id'])
        raise HTTPException(401, "Credenciales incorrectas.")
    await reset_ip_rate_limit(db, ip)
    await db.execute("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = $1", user['id'])
    token_nuevo = secrets.token_hex(32)
    await db.execute("INSERT INTO sesiones_activas (usuario_id, token_sesion, expira_en) VALUES ($1, $2, $3)", user['id'], hash_token(token_nuevo), datetime.now() + timedelta(hours=8))
    response.set_cookie(key="session_token", value=token_nuevo, httponly=True, secure=True, samesite='lax', max_age=28800)
    response.set_cookie(key="user_rol", value=str(user['rol']), httponly=False, secure=True, samesite='lax', max_age=28800)
    return {"status": "success", "rol": user['rol'], "redirect": f"/{user['rol']}.html"}


@router.post("/logout")
async def logout(request: Request, response: Response, db: asyncpg.Connection = Depends(get_db)):
    token = request.cookies.get("session_token")
    if token: await db.execute("DELETE FROM sesiones_activas WHERE token_sesion = $1", hash_token(token))
    response.delete_cookie("session_token"); response.delete_cookie("user_rol")
    return {"status": "success"}


@router.get("/setup/status")
async def setup_status(db: asyncpg.Connection = Depends(get_db)):
    count = await db.fetchval("SELECT COUNT(*) FROM usuarios")
    return {"needs_setup": (count or 0) == 0}


@router.post("/setup/admin")
async def setup_admin(data: SetupAdminRequest, response: Response, db: asyncpg.Connection = Depends(get_db)):
    if not SETUP_TOKEN or not secrets.compare_digest(data.token.strip(), SETUP_TOKEN):
        raise HTTPException(403, "Token de instalación inválido.")

    count = await db.fetchval("SELECT COUNT(*) FROM usuarios")
    if (count or 0) > 0:
        raise HTTPException(403, "La configuración inicial ya fue completada.")

    username = data.username.strip()
    full_name = data.full_name.strip()
    password = data.password
    if not username or not full_name:
        raise HTTPException(400, "Complete todos los campos.")
    if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", password):
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres, con 1 mayúscula y 1 número.")

    password_hash = await asyncio.to_thread(bcrypt.hashpw, password.encode(), bcrypt.gensalt())
    user_id = await db.fetchval(
        "INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo) VALUES ($1,$2,$3,'admin',TRUE) RETURNING id",
        username, password_hash.decode(), full_name
    )

    token_nuevo = secrets.token_hex(32)
    await db.execute("INSERT INTO sesiones_activas (usuario_id, token_sesion, expira_en) VALUES ($1, $2, $3)", user_id, hash_token(token_nuevo), datetime.now() + timedelta(hours=8))
    response.set_cookie(key="session_token", value=token_nuevo, httponly=True, secure=True, samesite='lax', max_age=28800)
    response.set_cookie(key="user_rol", value="admin", httponly=False, secure=True, samesite='lax', max_age=28800)
    return {"status": "success", "redirect": "/admin.html"}


# --- INTEGRADOR GOOGLE OAUTH2 ---
@router.get("/auth/google/url")
async def get_google_auth_url(response: Response, db: asyncpg.Connection = Depends(get_db)):
    cfg = await db.fetchrow("SELECT google_oauth_enabled, google_client_id, google_redirect_url FROM configuracion_sistema WHERE id=1")
    if not cfg or not cfg['google_oauth_enabled']: raise HTTPException(400, "Autenticacion Google deshabilitada.")
    # Parametro state (anti-CSRF de login): se guarda en una cookie y se verifica en el callback,
    # para que un atacante no pueda forzar el ingreso con SU codigo de Google en la sesion ajena.
    state = secrets.token_urlsafe(24)
    response.set_cookie(key="oauth_state", value=state, httponly=True, secure=True, samesite='lax', max_age=600)
    params = urlencode({"response_type": "code", "client_id": cfg['google_client_id'], "redirect_uri": cfg['google_redirect_url'], "scope": "openid email profile", "state": state})
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"}


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", db: asyncpg.Connection = Depends(get_db)):
    if not code: return RedirectResponse(url="/index.html?error=Codigo+Google+ausente")
    # Verificacion del state contra la cookie emitida al iniciar el flujo.
    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or not state or not secrets.compare_digest(state, cookie_state):
        return RedirectResponse(url="/index.html?error=Estado+de+sesion+invalido")
    cfg = await db.fetchrow("SELECT google_client_id, google_client_secret, google_redirect_url FROM configuracion_sistema WHERE id=1")
    async with httpx.AsyncClient() as client:
        res_token = await client.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": cfg['google_client_id'], "client_secret": cfg['google_client_secret'], "redirect_uri": cfg['google_redirect_url'], "grant_type": "authorization_code"})
        if res_token.status_code != 200: return RedirectResponse(url="/index.html?error=Fallo+intercambio+tokens")
        user_info = (await client.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {res_token.json()['access_token']}"})).json()
    email = user_info.get("email")
    user = await db.fetchrow("SELECT id, rol FROM usuarios WHERE username = $1 AND activo = TRUE", email)
    if not user:
        if not await db.fetchval("SELECT 1 FROM solicitudes_registro WHERE email = $1", email):
            await db.execute("INSERT INTO solicitudes_registro (email, nombre_completo) VALUES ($1, $2)", email, user_info.get("name", "Usuario"))
        return RedirectResponse(url="/index.html?status=pending")
    token_nuevo = secrets.token_hex(32)
    await db.execute("INSERT INTO sesiones_activas (usuario_id, token_sesion, expira_en) VALUES ($1, $2, $3)", user['id'], hash_token(token_nuevo), datetime.now() + timedelta(hours=8))
    response = RedirectResponse(url=f"/{user['rol']}.html")
    response.set_cookie(key="session_token", value=token_nuevo, httponly=True, secure=True, samesite='lax', max_age=28800)
    response.set_cookie(key="user_rol", value=str(user['rol']), httponly=False, secure=True, samesite='lax', max_age=28800)
    response.delete_cookie("oauth_state")
    return response
