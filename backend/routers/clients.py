from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from database import get_db, require_role

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


class DireccionClienteCreate(BaseModel):
    alias: str
    calle: str
    altura: str
    ciudad: str = ""
    codigo_postal: str = ""
    provincia: str = ""
    latitud: float = -34.6037
    longitud: float = -58.3816


@router.post("/clientes")
async def create_cliente_transaccional(data: ClienteTransaccionalCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin', 'cliente']))):
    if await db.fetchval("SELECT 1 FROM clientes WHERE cuit_rut = $1", data.cuit_rut): raise HTTPException(400, "CUIT Duplicado.")
    cfg = await db.fetchrow("SELECT ciudad_defecto, provincia_defecto FROM configuracion_sistema WHERE id=1")
    ciudad = data.ciudad if data.ciudad else cfg['ciudad_defecto']
    provincia = data.provincia if data.provincia else cfg['provincia_defecto']
    async with db.transaction():
        c_id = await db.fetchval("INSERT INTO clientes (razon_social, cuit_rut, activo) VALUES ($1, $2, TRUE) RETURNING id", data.razon_social, data.cuit_rut)
        await db.execute("INSERT INTO direcciones_cliente (cliente_id, alias, calle, altura, ciudad, codigo_postal, provincia, latitud, longitud) VALUES ($1, $2, $3, $4, $5, $6, $7, -34.6037, -58.3816)", c_id, data.alias, data.calle, data.altura, ciudad, data.codigo_postal, provincia)
    return {"status": "success"}


@router.get("/clientes")
async def get_clientes(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT id, razon_social, cuit_rut, activo FROM clientes ORDER BY razon_social ASC")]


@router.get("/clientes/{cliente_id}/direcciones")
async def get_direcciones_cliente(cliente_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT * FROM direcciones_cliente WHERE cliente_id = $1", cliente_id)]
