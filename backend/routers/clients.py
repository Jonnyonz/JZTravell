from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Clientes"])


class ClienteTransaccionalCreate(BaseModel):
    razon_social: str
    cuit_rut: str
    alias: str
    calle: str
    altura: str
    ciudad: Optional[str] = None
    codigo_postal: str
    provincia: Optional[str] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None


class DireccionCliente(BaseModel):
    alias: str
    calle: str
    altura: str
    ciudad: str = ""
    codigo_postal: str = ""
    provincia: str = ""
    # Sin coordenadas la direccion queda "sin ubicar" (no entra en el calculo de km).
    latitud: Optional[float] = None
    longitud: Optional[float] = None


def _puede_ver_cliente(user, cliente_id: int) -> bool:
    """Admin ve todos; un usuario cliente solo la empresa a la que pertenece."""
    return user['rol'] == 'admin' or (user['rol'] == 'cliente' and user.get('cliente_id') == cliente_id)


@router.post("/clientes")
async def create_cliente_transaccional(data: ClienteTransaccionalCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if await db.fetchval("SELECT 1 FROM clientes WHERE cuit_rut = $1", data.cuit_rut): raise HTTPException(400, "CUIT Duplicado.")
    cfg = await db.fetchrow("SELECT ciudad_defecto, provincia_defecto FROM configuracion_sistema WHERE id=1")
    ciudad = data.ciudad if data.ciudad else cfg['ciudad_defecto']
    provincia = data.provincia if data.provincia else cfg['provincia_defecto']
    async with db.transaction():
        c_id = await db.fetchval("INSERT INTO clientes (razon_social, cuit_rut, activo) VALUES ($1, $2, TRUE) RETURNING id", data.razon_social, data.cuit_rut)
        await db.execute("INSERT INTO direcciones_cliente (cliente_id, alias, calle, altura, ciudad, codigo_postal, provincia, latitud, longitud) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)", c_id, data.alias, data.calle, data.altura, ciudad, data.codigo_postal, provincia, data.latitud, data.longitud)
    return {"status": "success", "id": c_id}


@router.get("/clientes")
async def get_clientes(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT id, razon_social, cuit_rut, activo FROM clientes ORDER BY razon_social ASC")]


@router.get("/clientes/{cliente_id}/direcciones")
async def get_direcciones_cliente(cliente_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if not _puede_ver_cliente(user, cliente_id):
        raise HTTPException(403, "Acceso denegado.")
    return [dict(r) for r in await db.fetch("SELECT * FROM direcciones_cliente WHERE cliente_id = $1 ORDER BY alias", cliente_id)]


@router.post("/clientes/{cliente_id}/direcciones")
async def create_direccion_cliente(cliente_id: int, data: DireccionCliente, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if not _puede_ver_cliente(user, cliente_id):
        raise HTTPException(403, "Acceso denegado.")
    if not await db.fetchval("SELECT 1 FROM clientes WHERE id = $1", cliente_id):
        raise HTTPException(404, "Cliente no encontrado.")
    d_id = await db.fetchval("INSERT INTO direcciones_cliente (cliente_id, alias, calle, altura, ciudad, codigo_postal, provincia, latitud, longitud) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id", cliente_id, data.alias, data.calle, data.altura, data.ciudad, data.codigo_postal, data.provincia, data.latitud, data.longitud)
    return {"status": "success", "id": d_id}


@router.put("/clientes/{cliente_id}/direcciones/{direccion_id}")
async def update_direccion_cliente(cliente_id: int, direccion_id: int, data: DireccionCliente, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if not _puede_ver_cliente(user, cliente_id):
        raise HTTPException(403, "Acceso denegado.")
    r = await db.execute("UPDATE direcciones_cliente SET alias=$1, calle=$2, altura=$3, ciudad=$4, codigo_postal=$5, provincia=$6, latitud=$7, longitud=$8 WHERE id=$9 AND cliente_id=$10", data.alias, data.calle, data.altura, data.ciudad, data.codigo_postal, data.provincia, data.latitud, data.longitud, direccion_id, cliente_id)
    if r.endswith(" 0"):
        raise HTTPException(404, "Direccion no encontrada.")
    return {"status": "success"}
