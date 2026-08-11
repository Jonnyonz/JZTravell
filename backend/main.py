import os, asyncio, secrets, httpx, json, datetime, math, re
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import asyncpg
import bcrypt

app = FastAPI(title="JZ Tech Solutions - API Logística")
db_pool = None

@app.on_event("startup")
async def startup():
    global db_pool
    db_pool = await asyncpg.create_pool(
        user=os.getenv("POSTGRES_USER", "jzadmin"), password=os.getenv("POSTGRES_PASSWORD", "jzsecret_secure_password"),
        database=os.getenv("POSTGRES_DB", "jzflete_db"), host=os.getenv("POSTGRES_HOST", "db"), min_size=2, max_size=10
    )

@app.on_event("shutdown")
async def shutdown(): await db_pool.close()

async def get_db():
    async with db_pool.acquire() as connection: yield connection

# --- MODELOS DE DATOS ---
class LoginRequest(BaseModel): username: str; password: str
class UsuarioRegister(BaseModel): username: str; password: str; nombre_completo: str; rol: str
class UsuarioEdit(BaseModel): nombre_completo: str; rol: str; activo: bool; password: Optional[str] = None
class SolicitudProcesar(BaseModel): email: str; rol: str; nombre_completo: str; aprobar: bool
class SucursalCreate(BaseModel): nombre: str; calle: str = ""; altura: str = ""; ciudad: str = ""; codigo_postal: str = ""; provincia: str = ""; latitud: float = -34.6037; longitud: float = -58.3816
class SucursalEdit(BaseModel): nombre: str; calle: str; altura: str; ciudad: str; codigo_postal: str; provincia: str; latitud: float; longitud: float; activa: bool
class ClienteTransaccionalCreate(BaseModel): razon_social: str; cuit_rut: str; alias: str; calle: str; altura: str; ciudad: Optional[str] = None; codigo_postal: str; provincia: Optional[str] = None
class DireccionClienteCreate(BaseModel): alias: str; calle: str; altura: str; ciudad: str = ""; codigo_postal: str = ""; provincia: str = ""; latitud: float = -34.6037; longitud: float = -58.3816

class VehiculoCreate(BaseModel): patente_identificador: str; marca_modelo: str; tipo: str; autonomia_maxima_km: float; capacidad_volumen_m3: float = 0.0
class VehiculoEdit(BaseModel): patente_identificador: str; marca_modelo: str; tipo: str; autonomia_maxima_km: float; capacidad_volumen_m3: float; estado: str

class ConfigUpdate(BaseModel): 
    nombre_empresa: str; gps_polling_sec: int; google_oauth_enabled: bool; google_client_id: Optional[str] = None; google_client_secret: Optional[str] = None; google_redirect_url: Optional[str] = None
    ciudad_defecto: str; provincia_defecto: str; pais_defecto: str; manejar_volumenes: bool

class EstadoUpdate(BaseModel): estado: str
class GPSData(BaseModel): lat: float; lon: float
class ConsolidarRequest(BaseModel): flete_ids: List[int]

class DocFlete(BaseModel):
    tipo_documento: str; doc_id: str = ""; punto_venta: str = ""; numero: str = ""; destino_tipo: str; destino_id: int = 0
    destino_alias: str = ""; destino_calle: str = ""; destino_altura: str = ""; destino_ciudad: str = ""; destino_codigo_postal: str = ""; destino_provincia: str = ""
    bultos: int = 1; volumen_m3: float = 0.0

class FleteCreate(BaseModel): origen_id: int; vehiculo_id: int; prioridad: str; distancia_estimada_km: float = 0.0; documentos: List[DocFlete]

# --- AUXILIARES MATEMÁTICOS ---
def calcular_distancia_haversine(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2): return 0.0
    rad = math.pi / 180
    dlat = (lat2 - lat1) * rad; dlon = (lon2 - lon1) * rad
    a = math.sin(dlat/2)**2 + math.cos(lat1*rad) * math.cos(lat2*rad) * math.sin(dlon/2)**2
    return 6371.0 * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

def optimizar_secuencia_paradas(origen_lat, origen_lon, paradas):
    if not paradas: return []
    ruta_optimizada = []; pendientes = list(paradas)
    lat_actual, lon_actual = origen_lat, origen_lon
    while pendientes:
        proxima = min(pendientes, key=lambda p: calcular_distancia_haversine(lat_actual, lon_actual, p.get('latitud', -34.6037), p.get('longitud', -58.3816)))
        pendientes.remove(proxima)
        ruta_optimizada.append(proxima)
        lat_actual, lon_actual = proxima.get('latitud', -34.6037), proxima.get('longitud', -58.3816)
    return ruta_optimizada

# --- MIDDLEWARE DE SEGURIDAD ---
@app.middleware("http")
async def security_and_csrf_middleware(request: Request, call_next):
    if request.method in ["POST", "PUT", "DELETE"]:
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token or csrf_token != request.cookies.get("csrf_token"): 
            return JSONResponse(status_code=403, content={"detail": "Accion rechazada por validacion de seguridad CSRF."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if "csrf_token" not in request.cookies: 
        response.set_cookie(key="csrf_token", value=secrets.token_urlsafe(32), httponly=False, secure=True, samesite='lax')
    return response

# --- CONTROL DE IDENTIDAD ---
async def get_current_user(request: Request, db: asyncpg.Connection = Depends(get_db)):
    token_sesion = request.cookies.get("session_token")
    if not token_sesion: raise HTTPException(401, "Ausencia de credenciales.")
    registro = await db.fetchrow("SELECT u.id, u.username, u.rol FROM sesiones_activas s JOIN usuarios u ON s.usuario_id = u.id WHERE s.token_sesion = $1 AND s.expira_en > NOW() AND u.activo = TRUE", token_sesion)
    if not registro: raise HTTPException(401, "Sesion invalida.")
    return dict(registro)

def require_role(allowed_roles: List[str]):
    async def role_checker(user: dict = Depends(get_current_user)):
        if user['rol'] not in allowed_roles: raise HTTPException(403, "Acceso denegado.")
        return user
    return role_checker

# --- ENDPOINTS CONTROL DE ACCESO ---
@app.post("/api/login")
async def login(data: LoginRequest, response: Response, db: asyncpg.Connection = Depends(get_db)):
    user = await db.fetchrow("SELECT id, password_hash, rol, intentos_fallidos, bloqueado_hasta FROM usuarios WHERE username = $1 AND activo = TRUE", data.username)
    if not user: raise HTTPException(401, "Credenciales incorrectas.")
    if user['bloqueado_hasta'] and user['bloqueado_hasta'] > datetime.datetime.now(): raise HTTPException(423, "Cuenta bloqueada temporalmente.")
    if not await asyncio.to_thread(bcrypt.checkpw, data.password.encode(), user['password_hash'].encode()):
        intentos = user['intentos_fallidos'] + 1
        if intentos >= 5:
            await db.execute("UPDATE usuarios SET intentos_fallidos = $1, bloqueado_hasta = $2 WHERE id = $3", intentos, datetime.datetime.now() + datetime.timedelta(minutes=15), user['id'])
            raise HTTPException(423, "Cuenta bloqueada por 15 minutos.")
        await db.execute("UPDATE usuarios SET intentos_fallidos = $1 WHERE id = $2", intentos, user['id'])
        raise HTTPException(401, "Credenciales incorrectas.")
    await db.execute("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = $1", user['id'])
    token_nuevo = secrets.token_hex(32)
    await db.execute("INSERT INTO sesiones_activas (usuario_id, token_sesion, expira_en) VALUES ($1, $2, $3)", user['id'], token_nuevo, datetime.datetime.now() + datetime.timedelta(hours=8))
    response.set_cookie(key="session_token", value=token_nuevo, httponly=True, secure=True, samesite='lax', max_age=28800)
    response.set_cookie(key="user_rol", value=str(user['rol']), httponly=False, secure=True, samesite='lax', max_age=28800)
    return {"status": "success", "rol": user['rol'], "redirect": f"/{user['rol']}.html"}

@app.post("/api/logout")
async def logout(request: Request, response: Response, db: asyncpg.Connection = Depends(get_db)):
    token = request.cookies.get("session_token")
    if token: await db.execute("DELETE FROM sesiones_activas WHERE token_sesion = $1", token)
    response.delete_cookie("session_token"); response.delete_cookie("user_rol")
    return {"status": "success"}

# --- INTEGRADOR GOOGLE OAUTH2 ---
@app.get("/api/auth/google/url")
async def get_google_auth_url(db: asyncpg.Connection = Depends(get_db)):
    cfg = await db.fetchrow("SELECT google_oauth_enabled, google_client_id, google_redirect_url FROM configuracion_sistema WHERE id=1")
    if not cfg or not cfg['google_oauth_enabled']: raise HTTPException(400, "Autenticacion Google deshabilitada.")
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id={cfg['google_client_id']}&redirect_uri={cfg['google_redirect_url']}&scope=openid%20email%20profile"}

@app.get("/api/auth/google/callback")
async def google_callback(code: str, db: asyncpg.Connection = Depends(get_db)):
    if not code: return RedirectResponse(url="/index.html?error=Codigo+Google+ausente")
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
    await db.execute("INSERT INTO sesiones_activas (usuario_id, token_sesion, expira_en) VALUES ($1, $2, $3)", user['id'], token_nuevo, datetime.datetime.now() + datetime.timedelta(hours=8))
    response = RedirectResponse(url=f"/{user['rol']}.html")
    response.set_cookie(key="session_token", value=token_nuevo, httponly=True, secure=True, samesite='lax', max_age=28800)
    response.set_cookie(key="user_rol", value=str(user['rol']), httponly=False, secure=True, samesite='lax', max_age=28800)
    return response

# --- ADMINISTRACIÓN GLOBAL ---
@app.get("/api/config")
async def get_config(db: asyncpg.Connection = Depends(get_db)):
    return dict(await db.fetchrow("SELECT nombre_empresa, gps_polling_sec, google_oauth_enabled, google_client_id, google_redirect_url, ciudad_defecto, provincia_defecto, pais_defecto, manejar_volumenes FROM configuracion_sistema WHERE id=1"))

@app.post("/api/config")
async def update_config(data: ConfigUpdate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("""
        UPDATE configuracion_sistema SET nombre_empresa=$1, gps_polling_sec=$2, google_oauth_enabled=$3, google_client_id=$4, google_client_secret=$5, google_redirect_url=$6,
        ciudad_defecto=$7, provincia_defecto=$8, pais_defecto=$9, manejar_volumenes=$10 WHERE id=1
    """, data.nombre_empresa, data.gps_polling_sec, data.google_oauth_enabled, data.google_client_id, data.google_client_secret, data.google_redirect_url, data.ciudad_defecto, data.provincia_defecto, data.pais_defecto, data.manejar_volumenes)
    return {"status": "success"}

@app.get("/api/usuarios")
async def get_usuarios(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))): 
    return [dict(r) for r in await db.fetch("SELECT username, nombre_completo, rol, activo FROM usuarios ORDER BY username ASC")]

@app.post("/api/usuarios")
async def register_usuario(data: UsuarioRegister, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", data.password): raise HTTPException(400, "Contraseña no valida.")
    password_hash = await asyncio.to_thread(bcrypt.hashpw, data.password.encode(), bcrypt.gensalt())
    await db.execute("INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo) VALUES ($1, $2, $3, $4, TRUE)", data.username, password_hash.decode(), data.nombre_completo, data.rol)
    return {"status": "success"}

@app.put("/api/usuarios/{username}")
async def modificar_usuario(username: str, data: UsuarioEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if data.password:
        if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", data.password): raise HTTPException(400, "Contraseña invalida.")
        pw_hash = await asyncio.to_thread(bcrypt.hashpw, data.password.encode(), bcrypt.gensalt())
        await db.execute("UPDATE usuarios SET password_hash=$1, nombre_completo=$2, rol=$3, activo=$4 WHERE username=$5", pw_hash.decode(), data.nombre_completo, data.rol, data.activo, username)
    else:
        await db.execute("UPDATE usuarios SET nombre_completo=$1, rol=$2, activo=$3 WHERE username=$4", data.nombre_completo, data.rol, data.activo, username)
    return {"status": "success"}

# --- GESTIÓN DE FLOTA (Edición, Volumen y Bajas Lógicas) ---
@app.get("/api/vehiculos")
async def get_vehiculos(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)): 
    if user['rol'] == 'admin':
        return [dict(r) for r in await db.fetch("SELECT id, patente_identificador, marca_modelo, tipo, autonomia_maxima_km, estado, capacidad_volumen_m3 FROM flota_vehiculos ORDER BY patente_identificador ASC")]
    return [dict(r) for r in await db.fetch("SELECT id, patente_identificador, marca_modelo, tipo, autonomia_maxima_km, estado, capacidad_volumen_m3 FROM flota_vehiculos WHERE estado != 'baja' ORDER BY patente_identificador ASC")]

@app.post("/api/vehiculos")
async def create_vehiculo(data: VehiculoCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("INSERT INTO flota_vehiculos (patente_identificador, marca_modelo, tipo, autonomia_maxima_km, capacidad_volumen_m3, estado) VALUES ($1, $2, $3, $4, $5, 'activo')", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3)
    return {"status": "success"}

@app.put("/api/vehiculos/{vehiculo_id}")
async def update_vehiculo(vehiculo_id: int, data: VehiculoEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("UPDATE flota_vehiculos SET patente_identificador=$1, marca_modelo=$2, tipo=$3, autonomia_maxima_km=$4, capacidad_volumen_m3=$5, estado=$6 WHERE id=$7", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3, data.estado, vehiculo_id)
    return {"status": "success"}

# --- GESTIÓN DE SUCURSALES (Faltantes Añadidos) ---
@app.get("/api/sucursales")
async def get_sucursales(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    return [dict(r) for r in await db.fetch("SELECT * FROM sucursales ORDER BY nombre ASC")]

@app.post("/api/sucursales")
async def create_sucursal(data: SucursalCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("INSERT INTO sucursales (nombre, calle, altura, ciudad, codigo_postal, provincia, latitud, longitud, activa) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE)", data.nombre, data.calle, data.altura, data.ciudad, data.codigo_postal, data.provincia, data.latitud, data.longitud)
    return {"status": "success"}

@app.put("/api/sucursales/{sucursal_id}")
async def update_sucursal(sucursal_id: int, data: SucursalEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("UPDATE sucursales SET nombre=$1, calle=$2, altura=$3, ciudad=$4, codigo_postal=$5, provincia=$6, activa=$7 WHERE id=$8", data.nombre, data.calle, data.altura, data.ciudad, data.codigo_postal, data.provincia, data.activa, sucursal_id)
    return {"status": "success"}

# --- DESTINATARIOS ---
@app.post("/api/clientes")
async def create_cliente_transaccional(data: ClienteTransaccionalCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin', 'cliente']))):
    if await db.fetchval("SELECT 1 FROM clientes WHERE cuit_rut = $1", data.cuit_rut): raise HTTPException(400, "CUIT Duplicado.")
    cfg = await db.fetchrow("SELECT ciudad_defecto, provincia_defecto FROM configuracion_sistema WHERE id=1")
    ciudad = data.ciudad if data.ciudad else cfg['ciudad_defecto']
    provincia = data.provincia if data.provincia else cfg['provincia_defecto']
    async with db.transaction():
        c_id = await db.fetchval("INSERT INTO clientes (razon_social, cuit_rut, activo) VALUES ($1, $2, TRUE) RETURNING id", data.razon_social, data.cuit_rut)
        await db.execute("INSERT INTO direcciones_cliente (cliente_id, alias, calle, altura, ciudad, codigo_postal, provincia, latitud, longitud) VALUES ($1, $2, $3, $4, $5, $6, $7, -34.6037, -58.3816)", c_id, data.alias, data.calle, data.altura, ciudad, data.codigo_postal, provincia)
    return {"status": "success"}

@app.get("/api/clientes")
async def get_clientes(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)): return [dict(r) for r in await db.fetch("SELECT id, razon_social, cuit_rut, activo FROM clientes ORDER BY razon_social ASC")]
@app.get("/api/clientes/{cliente_id}/direcciones")
async def get_direcciones_cliente(cliente_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)): return [dict(r) for r in await db.fetch("SELECT * FROM direcciones_cliente WHERE cliente_id = $1", cliente_id)]

@app.get("/api/admin/solicitudes")
async def listar_solicitudes_registro(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))): return [dict(r) for r in await db.fetch("SELECT * FROM solicitudes_registro ORDER BY creado_en DESC")]
@app.post("/api/admin/solicitudes/procesar")
async def procesar_solicitud_registro(data: SolicitudProcesar, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    async with db.transaction():
        await db.execute("DELETE FROM solicitudes_registro WHERE email = $1", data.email)
        if data.aprobar:
            hash_dummy = await asyncio.to_thread(bcrypt.hashpw, secrets.token_urlsafe(24).encode(), bcrypt.gensalt())
            await db.execute("INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo) VALUES ($1, $2, $3, $4, TRUE)", data.email, hash_dummy.decode(), data.nombre_completo, data.rol)
    return {"status": "success"}

@app.get("/api/tipos_documentos")
async def get_tipos_docs(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)): return [dict(r) for r in await db.fetch("SELECT * FROM tipos_documentos WHERE activo=TRUE")]

@app.post("/api/gps")
async def update_gps(data: GPSData, request: Request, db: asyncpg.Connection = Depends(get_db)):
    user=await get_current_user(request, db)
    await db.execute("UPDATE usuarios SET lat_actual = $1, lon_actual = $2, ultima_conexion = NOW() WHERE id = $3::uuid", data.lat, data.lon, user['id'])
    return {"status": "ok"}

# --- DESPACHO DE FLETES ---
@app.get("/api/fletes")
async def get_fletes(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    query = "SELECT f.id, f.prioridad, f.estado, f.distancia_total_km, f.creado_en, f.chofer_id, v.patente_identificador, v.marca_modelo, s.nombre as origen, s.calle as origen_calle, s.altura as origen_altura, s.ciudad as origen_ciudad, s.provincia as origen_provincia, s.latitud as origen_lat, s.longitud as origen_lon, COALESCE(u.nombre_completo, 'Desconocido') as solicitante FROM fletes f LEFT JOIN flota_vehiculos v ON f.vehiculo_id = v.id LEFT JOIN sucursales s ON f.origen_id = s.id LEFT JOIN usuarios u ON f.solicitante_id = u.id ORDER BY f.id DESC LIMIT 100"
    fletes = [dict(r) for r in await db.fetch(query)]
    for f in fletes:
        f['creado_en'] = f['creado_en'].isoformat()
        if f['chofer_id']: f['chofer_id'] = str(f['chofer_id'])
        f['paradas'] = [dict(p) for p in await db.fetch("SELECT * FROM documentos_flete WHERE flete_id = $1 ORDER BY orden_ruta ASC", f['id'])]
    return fletes

@app.post("/api/fletes")
async def create_flete(data: FleteCreate, request: Request, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    cfg = await db.fetchrow("SELECT manejar_volumenes FROM configuracion_sistema WHERE id=1")
    if cfg['manejar_volumenes']:
        v_cap = await db.fetchval("SELECT capacidad_volumen_m3 FROM flota_vehiculos WHERE id = $1", data.vehiculo_id)
        v_req = sum(doc.volumen_m3 for doc in data.documentos)
        if v_req > v_cap: raise HTTPException(400, f"Capacidad volumetrica excedida. Requerido: {v_req} m3 | Disponible: {v_cap} m3.")
        
    async with db.transaction():
        flete_id = await db.fetchval("INSERT INTO fletes (solicitante_id, vehiculo_id, origen_id, prioridad, distancia_total_km) VALUES ($1::uuid, $2, $3, $4, $5) RETURNING id", user['id'], data.vehiculo_id, data.origen_id, data.prioridad, data.distancia_estimada_km)
        for idx, doc in enumerate(data.documentos): 
            await db.execute("INSERT INTO documentos_flete (flete_id, tipo_documento, doc_id, punto_venta, numero, destino_tipo, destino_id, destino_alias, destino_calle, destino_altura, destino_ciudad, destino_codigo_postal, destino_provincia, orden_ruta, bultos, volumen_m3) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)", flete_id, doc.tipo_documento, doc.doc_id, doc.punto_venta, doc.numero, doc.destino_tipo, doc.destino_id, doc.destino_alias, doc.destino_calle, doc.destino_altura, doc.destino_ciudad, doc.destino_codigo_postal, doc.destino_provincia, idx + 1, doc.bultos, doc.volumen_m3)
    return {"status": "success"}

@app.post("/api/fletes/consolidar")
async def consolidar_y_optimizar(data: ConsolidarRequest, request: Request, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['chofer']))):
    if not data.flete_ids: raise HTTPException(400, "Lista vacía.")
    async with db.transaction():
        fletes_data = await db.fetch("SELECT f.id, s.latitud, s.longitud FROM fletes f JOIN sucursales s ON f.origen_id = s.id WHERE f.id = ANY($1::int[])", data.flete_ids)
        if not fletes_data: raise HTTPException(400, "Fletes no identificados.")
        orig_lat, orig_lon = fletes_data[0]['latitud'], fletes_data[0]['longitud']
        paradas = [dict(p) for p in await db.fetch("SELECT * FROM documentos_flete WHERE flete_id = ANY($1::int[])", data.flete_ids)]
        for p in paradas:
            coords = await db.fetchrow("SELECT latitud, longitud FROM sucursales WHERE id = $1" if p['destino_tipo'] == 'sucursal' else "SELECT latitud, longitud FROM direcciones_cliente WHERE id = $1", p['destino_id'])
            p['latitud'] = coords['latitud'] if coords else -34.6037; p['longitud'] = coords['longitud'] if coords else -58.3816
        paradas_ordenadas = optimizar_secuencia_paradas(orig_lat, orig_lon, paradas)
        await db.execute("UPDATE fletes SET estado = 'camino', chofer_id = $1::uuid WHERE id = ANY($2::int[])", user['id'], data.flete_ids)
        for idx, p in enumerate(paradas_ordenadas): await db.execute("UPDATE documentos_flete SET orden_ruta = $1 WHERE id = $2", idx + 1, p['id'])
    return {"status": "success"}

@app.post("/api/fletes/{flete_id}/estado")
async def update_flete_estado(flete_id: int, data: EstadoUpdate, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    await db.execute("UPDATE fletes SET estado = $1 WHERE id = $2", data.estado, flete_id)
    return {"status": "success"}

# --- HOJA DE IMPRESIÓN ---
@app.get("/api/fletes/imprimir", response_class=HTMLResponse)
async def imprimir_hoja_ruta(ids: str, db: asyncpg.Connection = Depends(get_db)):
    flete_ids = [int(x) for x in ids.split(",")]
    cfg = await db.fetchrow("SELECT manejar_volumenes FROM configuracion_sistema WHERE id=1")
    fletes_rows = await db.fetch("SELECT f.id, s.nombre as origen, u.nombre_completo as chofer, v.patente_identificador, v.marca_modelo FROM fletes f LEFT JOIN sucursales s ON f.origen_id = s.id LEFT JOIN usuarios u ON f.chofer_id = u.id LEFT JOIN flota_vehiculos v ON f.vehiculo_id = v.id WHERE f.id = ANY($1::int[])", flete_ids)
    paradas_rows = await db.fetch("SELECT d.*, s.nombre as sucursal_origen FROM documentos_flete d JOIN fletes f ON d.flete_id = f.id JOIN sucursales s ON f.origen_id = s.id WHERE d.flete_id = ANY($1::int[]) ORDER BY d.orden_ruta ASC", flete_ids)
    fecha_actual = datetime.date.today().strftime("%d/%m/%Y"); codigo_control = str(fletes_rows[0]['id']).zfill(6)
    
    col_vol_header = '<th style="width: 8%; text-align:center;">Vol (m³)</th>' if cfg['manejar_volumenes'] else ''
    
    tabla_filas = ""
    for idx, p in enumerate(paradas_rows):
        bg = "background-color: #ffffff;" if idx % 2 == 0 else "background-color: #f7fafc;"
        col_vol_td = f'<td style="padding:8px; border:1px solid #cbd5e0; text-align:center;">{p["volumen_m3"]} m³</td>' if cfg['manejar_volumenes'] else ''
        tabla_filas += f'<tr style="{bg}"><td style="padding:8px; border:1px solid #cbd5e0; text-align:center;"><strong>{idx + 1}</strong></td><td style="padding:8px; border:1px solid #cbd5e0;">{p["tipo_documento"].upper()}<br><small style="color:#555;">N° {p["punto_venta"]}-{p["numero"]}</small></td><td style="padding:8px; border:1px solid #cbd5e0;">{p["sucursal_origen"]}</td><td style="padding:8px; border:1px solid #cbd5e0; text-align:center;"><strong>{p["bultos"]}</strong></td>{col_vol_td}<td style="padding:8px; border:1px solid #cbd5e0;">{p["destino_calle"]} {p["destino_altura"]}, {p["destino_ciudad"]}</td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td></tr>'
        
    return f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Despacho</title><style>@page {{ size: A4 portrait; margin: 15mm 10mm; }} body {{ font-family: system-ui, sans-serif; color: #333; margin: 0; padding: 0; font-size: 9pt; line-height: 1.4; }} .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }} .title {{ font-size: 18pt; font-weight: bold; text-transform: uppercase; }} .meta-table {{ width: 100%; border-collapse: collapse; background-color: #f8f9fa; border: 1px solid #e2e8f0; margin-bottom: 20px; }} .meta-table td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }} .section-title {{ font-size: 11pt; font-weight: bold; border-bottom: 2px solid #2d3748; padding-bottom: 4px; text-transform: uppercase; margin-bottom: 12px; }} .route-table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; }} .route-table th {{ background-color: #2d3748; color: white; padding: 8px 6px; font-size: 8pt; text-transform: uppercase; text-align: left; }} .signature-table {{ width: 100%; border-collapse: collapse; margin-top: 40px; }} .signature-box {{ width: 45%; text-align: center; }} .signature-space {{ height: 50px; border-bottom: 1px solid #4a5568; margin-bottom: 8px; }} .footer-brand {{ text-align: center; font-size: 7.5pt; color: #888; margin-top: 30px; }} .footer-brand a {{ color: #f06a25; text-decoration: none; font-weight: bold; }} </style></head><body onload="window.print()"><table class="header-table"><tr><td><div class="title">BazarChef</div><div>Control de Distribución y Logística</div></td><td style="text-align: right; font-size: 8.5pt; color: #555;"><strong>Documento Oficial de Despacho</strong><br>Fecha de Emisión: {fecha_actual}<br><span style="font-size: 18pt; font-weight: bold; color: #000; display: block; margin-top: 5px; letter-spacing: 1px;">N° {codigo_control}</span></td></tr></table><table class="meta-table"><tr><td><strong>Transportista:</strong></td><td>{fletes_rows[0]["chofer"] or "No asignado"}</td><td><strong>Vehículo / Patente:</strong></td><td>{fletes_rows[0]["marca_modelo"] or "S/D"} ({fletes_rows[0]["patente_identificador"] or "S/D"})</td></tr><tr><td><strong>Punto de Salida:</strong></td><td>{fletes_rows[0]["origen"]}</td><td><strong>Total Envíos:</strong></td><td>{len(fletes_rows)} Consolizados</td></tr><tr><td style="color: #4a5568; font-weight: bold; border-top: 2px solid #cbd5e0;">Hora de Inicio:</td><td style="border-top: 2px solid #cbd5e0;">______ : ______ hs</td><td style="color: #4a5568; font-weight: bold; border-top: 2px solid #cbd5e0;">Hora de Finalización:</td><td style="border-top: 2px solid #cbd5e0;">______ : ______ hs</td></tr></table><div class="section-title">Secuencia de Entrega Optimizada</div><table class="route-table"><thead><tr><th style="width: 4%; text-align:center;">Sec.</th><th style="width: 15%;">Referencia / Doc.</th><th style="width: 15%;">Origen</th><th style="width: 8%; text-align:center;">Bultos</th>{col_vol_header}<th style="width: 28%;">Dirección de Entrega</th><th style="width: 10%; text-align:center;">Hora Arribo</th><th style="width: 10%; text-align:center;">Hora Salida</th><th style="width: 10%;">Firma Recepción</th></tr></thead><tbody>{tabla_filas}</tbody></table><table class="signature-table"><tr><td class="signature-box"><div class="signature-space"></div><div>Firma del Transportista</div></td><td style="width: 10%;"></td><td class="signature-box"><div class="signature-space"></div><div>Control de Despacho Central</div></td></tr></table><div class="footer-brand">Versión 1.9<br>Powered by <a href="https://jz-tech.mywire.org" target="_blank">JZ Tech Solutions</a></div></body></html>'

app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
