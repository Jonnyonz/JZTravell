from fastapi import APIRouter, Depends
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Flota"])


class VehiculoCreate(BaseModel):
    patente_identificador: str
    marca_modelo: str
    tipo: str
    autonomia_maxima_km: float
    capacidad_volumen_m3: float = 0.0


class VehiculoEdit(BaseModel):
    patente_identificador: str
    marca_modelo: str
    tipo: str
    autonomia_maxima_km: float
    capacidad_volumen_m3: float
    estado: str


@router.get("/vehiculos")
async def get_vehiculos(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if user['rol'] == 'admin':
        return [dict(r) for r in await db.fetch("SELECT id, patente_identificador, marca_modelo, tipo, autonomia_maxima_km, estado, capacidad_volumen_m3 FROM flota_vehiculos ORDER BY patente_identificador ASC")]
    return [dict(r) for r in await db.fetch("SELECT id, patente_identificador, marca_modelo, tipo, autonomia_maxima_km, estado, capacidad_volumen_m3 FROM flota_vehiculos WHERE estado != 'baja' ORDER BY patente_identificador ASC")]


@router.post("/vehiculos")
async def create_vehiculo(data: VehiculoCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("INSERT INTO flota_vehiculos (patente_identificador, marca_modelo, tipo, autonomia_maxima_km, capacidad_volumen_m3, estado) VALUES ($1, $2, $3, $4, $5, 'activo')", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3)
    return {"status": "success"}


@router.put("/vehiculos/{vehiculo_id}")
async def update_vehiculo(vehiculo_id: int, data: VehiculoEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("UPDATE flota_vehiculos SET patente_identificador=$1, marca_modelo=$2, tipo=$3, autonomia_maxima_km=$4, capacidad_volumen_m3=$5, estado=$6 WHERE id=$7", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3, data.estado, vehiculo_id)
    return {"status": "success"}
