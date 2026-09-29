import asyncio
import secrets
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from database import get_db, require_role, hash_password

router = APIRouter(prefix="/api", tags=["Usuarios"])


class UsuarioRegister(BaseModel):
    username: str
    password: str
    nombre_completo: str
    rol: str


class UsuarioEdit(BaseModel):
    nombre_completo: str
    rol: str
    activo: bool
    password: Optional[str] = None


class SolicitudProcesar(BaseModel):
    email: str
    rol: str
    nombre_completo: str
    aprobar: bool


@router.get("/usuarios")
async def get_usuarios(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT username, nombre_completo, rol, activo FROM usuarios ORDER BY username ASC")]


@router.post("/usuarios")
async def register_usuario(data: UsuarioRegister, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", data.password): raise HTTPException(400, "Contraseña no valida.")
    password_hash = await asyncio.to_thread(hash_password, data.password)
    await db.execute("INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo) VALUES ($1, $2, $3, $4, TRUE)", data.username, password_hash, data.nombre_completo, data.rol)
    return {"status": "success"}


@router.put("/usuarios/{username}")
async def modificar_usuario(username: str, data: UsuarioEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if data.password:
        if not re.match(r"^(?=.*[0-9])(?=.*[A-Z]).{8,}$", data.password): raise HTTPException(400, "Contraseña invalida.")
        pw_hash = await asyncio.to_thread(hash_password, data.password)
        await db.execute("UPDATE usuarios SET password_hash=$1, nombre_completo=$2, rol=$3, activo=$4 WHERE username=$5", pw_hash, data.nombre_completo, data.rol, data.activo, username)
    else:
        await db.execute("UPDATE usuarios SET nombre_completo=$1, rol=$2, activo=$3 WHERE username=$4", data.nombre_completo, data.rol, data.activo, username)
    return {"status": "success"}


@router.get("/admin/solicitudes")
async def listar_solicitudes_registro(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT * FROM solicitudes_registro ORDER BY creado_en DESC")]


@router.post("/admin/solicitudes/procesar")
async def procesar_solicitud_registro(data: SolicitudProcesar, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    async with db.transaction():
        await db.execute("DELETE FROM solicitudes_registro WHERE email = $1", data.email)
        if data.aprobar:
            hash_dummy = await asyncio.to_thread(hash_password, secrets.token_urlsafe(24))
            await db.execute("INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo) VALUES ($1, $2, $3, $4, TRUE)", data.email, hash_dummy, data.nombre_completo, data.rol)
    return {"status": "success"}
