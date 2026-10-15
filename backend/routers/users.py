import asyncio
import secrets
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user, require_role, hash_password, revoke_user_sessions

router = APIRouter(prefix="/api", tags=["Usuarios"])

ROLES = ("admin", "chofer", "cliente")


class UsuarioRegister(BaseModel):
    username: str
    password: str
    nombre_completo: str
    rol: str
    # Empresa (tabla clientes) del usuario con rol cliente: ve los viajes y cotizaciones de esa empresa.
    cliente_id: Optional[int] = None


class UsuarioEdit(BaseModel):
    nombre_completo: str
    rol: str
    activo: bool
    password: Optional[str] = None
    cliente_id: Optional[int] = None


class SolicitudProcesar(BaseModel):
    email: str
    rol: str
    nombre_completo: str
    aprobar: bool


async def _validar_rol(db, rol: str, cliente_id: Optional[int]) -> Optional[int]:
    """Devuelve el cliente_id a guardar: solo los usuarios cliente pertenecen a una empresa."""
    if rol not in ROLES:
        raise HTTPException(400, "Rol invalido.")
    if rol != "cliente":
        return None
    if cliente_id is not None and not await db.fetchval("SELECT 1 FROM clientes WHERE id = $1", cliente_id):
        raise HTTPException(400, "La empresa cliente indicada no existe.")
    return cliente_id


@router.get("/me")
async def quien_soy(user=Depends(get_current_user)):
    """Usuario de la sesion: los paneles lo usan para saber su id y su rol real (la cookie user_rol es solo
    para la interfaz) y si mostrar los botones para cambiar de panel (solo admin)."""
    return {"id": str(user["id"]), "username": user["username"], "nombre_completo": user["nombre_completo"],
            "rol": user["rol"], "cliente_id": user["cliente_id"]}


@router.get("/usuarios")
async def get_usuarios(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT u.username, u.nombre_completo, u.rol, u.activo, u.cliente_id, c.razon_social AS empresa FROM usuarios u LEFT JOIN clientes c ON c.id = u.cliente_id ORDER BY u.username ASC")]


@router.post("/usuarios")
async def register_usuario(data: UsuarioRegister, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", data.password): raise HTTPException(400, "Contraseña no valida.")
    cliente_id = await _validar_rol(db, data.rol, data.cliente_id)
    if await db.fetchval("SELECT 1 FROM usuarios WHERE username = $1", data.username):
        raise HTTPException(400, "Ese nombre de usuario ya existe.")
    password_hash = await asyncio.to_thread(hash_password, data.password)
    await db.execute("INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo, cliente_id) VALUES ($1, $2, $3, $4, TRUE, $5)", data.username, password_hash, data.nombre_completo, data.rol, cliente_id)
    return {"status": "success"}


@router.put("/usuarios/{username}")
async def modificar_usuario(username: str, data: UsuarioEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    cliente_id = await _validar_rol(db, data.rol, data.cliente_id)
    if data.password:
        if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", data.password): raise HTTPException(400, "Contraseña invalida.")
        pw_hash = await asyncio.to_thread(hash_password, data.password)
        await db.execute("UPDATE usuarios SET password_hash=$1, nombre_completo=$2, rol=$3, activo=$4, cliente_id=$5 WHERE username=$6", pw_hash, data.nombre_completo, data.rol, data.activo, cliente_id, username)
    else:
        await db.execute("UPDATE usuarios SET nombre_completo=$1, rol=$2, activo=$3, cliente_id=$4 WHERE username=$5", data.nombre_completo, data.rol, data.activo, cliente_id, username)
    # Clave nueva o usuario desactivado: se cierran sus sesiones abiertas (en todos los dispositivos).
    if data.password or not data.activo:
        user_id = await db.fetchval("SELECT id FROM usuarios WHERE username=$1", username)
        if user_id:
            await revoke_user_sessions(db, user_id)
    return {"status": "success"}


@router.get("/admin/solicitudes")
async def listar_solicitudes_registro(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT * FROM solicitudes_registro ORDER BY creado_en DESC")]


@router.post("/admin/solicitudes/procesar")
async def procesar_solicitud_registro(data: SolicitudProcesar, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if data.aprobar and data.rol not in ROLES:
        raise HTTPException(400, "Rol invalido.")
    async with db.transaction():
        await db.execute("DELETE FROM solicitudes_registro WHERE email = $1", data.email)
        if data.aprobar:
            hash_dummy = await asyncio.to_thread(hash_password, secrets.token_urlsafe(24))
            await db.execute("INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo) VALUES ($1, $2, $3, $4, TRUE)", data.email, hash_dummy, data.nombre_completo, data.rol)
    return {"status": "success"}
