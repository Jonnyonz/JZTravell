from fastapi import APIRouter, Depends
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Sucursales"])


class SucursalCreate(BaseModel):
    nombre: str
    calle: str = ""
    altura: str = ""
    ciudad: str = ""
    codigo_postal: str = ""
    provincia: str = ""
    latitud: float = -34.6037
    longitud: float = -58.3816


class SucursalEdit(BaseModel):
    nombre: str
    calle: str
    altura: str
    ciudad: str
    codigo_postal: str
    provincia: str
    latitud: float
    longitud: float
    activa: bool


@router.get("/sucursales")
async def get_sucursales(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    return [dict(r) for r in await db.fetch("SELECT * FROM sucursales ORDER BY nombre ASC")]


@router.post("/sucursales")
async def create_sucursal(data: SucursalCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("INSERT INTO sucursales (nombre, calle, altura, ciudad, codigo_postal, provincia, latitud, longitud, activa) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE)", data.nombre, data.calle, data.altura, data.ciudad, data.codigo_postal, data.provincia, data.latitud, data.longitud)
    return {"status": "success"}


@router.put("/sucursales/{sucursal_id}")
async def update_sucursal(sucursal_id: int, data: SucursalEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("UPDATE sucursales SET nombre=$1, calle=$2, altura=$3, ciudad=$4, codigo_postal=$5, provincia=$6, activa=$7 WHERE id=$8", data.nombre, data.calle, data.altura, data.ciudad, data.codigo_postal, data.provincia, data.activa, sucursal_id)
    return {"status": "success"}
