from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Flota"])

COLUMNAS = ("id, patente_identificador, marca_modelo, tipo, autonomia_maxima_km, estado, capacidad_volumen_m3, "
            "usa_volumen, consumo_100km, costo_km, odometro_km")


class VehiculoCreate(BaseModel):
    patente_identificador: str
    marca_modelo: str
    tipo: str
    autonomia_maxima_km: float
    # Volumen propio de cada vehiculo: si usa_volumen, los fletes no pueden pasar capacidad_volumen_m3.
    usa_volumen: bool = False
    capacidad_volumen_m3: float = 0.0
    # Para cotizar: consumo cada 100 km (litros o kWh segun el motor) y costo de desgaste por km.
    consumo_100km: float = 0.0
    costo_km: float = 0.0
    odometro_km: float = 0.0


class VehiculoEdit(VehiculoCreate):
    estado: str


def _validar(data: VehiculoCreate):
    if data.tipo not in ("electrico", "combustion"):
        raise HTTPException(400, "Motor invalido.")
    if min(data.autonomia_maxima_km, data.capacidad_volumen_m3, data.consumo_100km, data.costo_km, data.odometro_km) < 0:
        raise HTTPException(400, "Los valores no pueden ser negativos.")
    if data.usa_volumen and data.capacidad_volumen_m3 <= 0:
        raise HTTPException(400, "Si el vehiculo trabaja por volumen, indique su capacidad en m3.")


@router.get("/vehiculos")
async def get_vehiculos(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if user['rol'] == 'admin':
        return [dict(r) for r in await db.fetch(f"SELECT {COLUMNAS} FROM flota_vehiculos ORDER BY patente_identificador ASC")]
    return [dict(r) for r in await db.fetch(f"SELECT {COLUMNAS} FROM flota_vehiculos WHERE estado != 'baja' ORDER BY patente_identificador ASC")]


@router.post("/vehiculos")
async def create_vehiculo(data: VehiculoCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    _validar(data)
    await db.execute("INSERT INTO flota_vehiculos (patente_identificador, marca_modelo, tipo, autonomia_maxima_km, capacidad_volumen_m3, usa_volumen, consumo_100km, costo_km, odometro_km, estado) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'activo')", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3, data.usa_volumen, data.consumo_100km, data.costo_km, data.odometro_km)
    return {"status": "success"}


@router.put("/vehiculos/{vehiculo_id}")
async def update_vehiculo(vehiculo_id: int, data: VehiculoEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    _validar(data)
    if data.estado not in ("activo", "mantenimiento", "baja"):
        raise HTTPException(400, "Estado invalido.")
    await db.execute("UPDATE flota_vehiculos SET patente_identificador=$1, marca_modelo=$2, tipo=$3, autonomia_maxima_km=$4, capacidad_volumen_m3=$5, estado=$6, usa_volumen=$7, consumo_100km=$8, costo_km=$9, odometro_km=$10 WHERE id=$11", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3, data.estado, data.usa_volumen, data.consumo_100km, data.costo_km, data.odometro_km, vehiculo_id)
    return {"status": "success"}
