from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from calculos import estado_vencimiento
from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Flota"])

COLUMNAS = ("id, patente_identificador, marca_modelo, tipo, autonomia_maxima_km, estado, capacidad_volumen_m3, "
            "usa_volumen, consumo_100km, costo_km, odometro_km, vtv_vence, seguro_vence, habilitacion_vence, "
            "service_cada_km, ultimo_service_km")
AVISO_SERVICE_KM = 500  # se avisa cuando faltan menos de estos km para el proximo service


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
    # Vencimientos: con alguno vencido el vehiculo no puede salir (se controla al tomar fletes).
    vtv_vence: Optional[date] = None
    seguro_vence: Optional[date] = None
    habilitacion_vence: Optional[date] = None
    # Mantenimiento: aviso de service cada tantos km (0 = sin aviso).
    service_cada_km: float = 0.0
    ultimo_service_km: float = 0.0


class VehiculoEdit(VehiculoCreate):
    estado: str


class Mantenimiento(BaseModel):
    fecha: Optional[date] = None
    odometro_km: Optional[float] = None
    tipo: str = "service"
    descripcion: str = ""
    costo: float = 0.0


def _validar(data: VehiculoCreate):
    if data.tipo not in ("electrico", "combustion"):
        raise HTTPException(400, "Motor invalido.")
    if min(data.autonomia_maxima_km, data.capacidad_volumen_m3, data.consumo_100km, data.costo_km, data.odometro_km,
           data.service_cada_km, data.ultimo_service_km) < 0:
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
    await db.execute("INSERT INTO flota_vehiculos (patente_identificador, marca_modelo, tipo, autonomia_maxima_km, capacidad_volumen_m3, usa_volumen, consumo_100km, costo_km, odometro_km, vtv_vence, seguro_vence, habilitacion_vence, service_cada_km, ultimo_service_km, estado) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'activo')", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3, data.usa_volumen, data.consumo_100km, data.costo_km, data.odometro_km, data.vtv_vence, data.seguro_vence, data.habilitacion_vence, data.service_cada_km, data.ultimo_service_km)
    return {"status": "success"}


@router.put("/vehiculos/{vehiculo_id}")
async def update_vehiculo(vehiculo_id: int, data: VehiculoEdit, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    _validar(data)
    if data.estado not in ("activo", "mantenimiento", "baja"):
        raise HTTPException(400, "Estado invalido.")
    await db.execute("UPDATE flota_vehiculos SET patente_identificador=$1, marca_modelo=$2, tipo=$3, autonomia_maxima_km=$4, capacidad_volumen_m3=$5, estado=$6, usa_volumen=$7, consumo_100km=$8, costo_km=$9, odometro_km=$10, vtv_vence=$11, seguro_vence=$12, habilitacion_vence=$13, service_cada_km=$14, ultimo_service_km=$15 WHERE id=$16", data.patente_identificador, data.marca_modelo, data.tipo, data.autonomia_maxima_km, data.capacidad_volumen_m3, data.estado, data.usa_volumen, data.consumo_100km, data.costo_km, data.odometro_km, data.vtv_vence, data.seguro_vence, data.habilitacion_vence, data.service_cada_km, data.ultimo_service_km, vehiculo_id)
    return {"status": "success"}


@router.get("/vehiculos/{vehiculo_id}/mantenimientos")
async def listar_mantenimientos(vehiculo_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    return [dict(r) for r in await db.fetch("SELECT * FROM mantenimientos WHERE vehiculo_id = $1 ORDER BY fecha DESC, id DESC", vehiculo_id)]


@router.post("/vehiculos/{vehiculo_id}/mantenimientos")
async def registrar_mantenimiento(vehiculo_id: int, data: Mantenimiento, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    if data.tipo not in ("service", "reparacion", "cubiertas", "otro"):
        raise HTTPException(400, "Tipo invalido.")
    if data.costo < 0 or (data.odometro_km is not None and data.odometro_km < 0):
        raise HTTPException(400, "Los valores no pueden ser negativos.")
    if not await db.fetchval("SELECT 1 FROM flota_vehiculos WHERE id = $1", vehiculo_id):
        raise HTTPException(404, "Vehiculo no encontrado.")
    async with db.transaction():
        m_id = await db.fetchval("INSERT INTO mantenimientos (vehiculo_id, fecha, odometro_km, tipo, descripcion, costo) VALUES ($1, COALESCE($2, CURRENT_DATE), $3, $4, $5, $6) RETURNING id",
                                 vehiculo_id, data.fecha, data.odometro_km, data.tipo, data.descripcion.strip(), data.costo)
        if data.odometro_km is not None:
            # Un service reinicia la cuenta para el proximo; cualquier registro con km actualiza el odometro.
            if data.tipo == "service":
                await db.execute("UPDATE flota_vehiculos SET ultimo_service_km = $1 WHERE id = $2", data.odometro_km, vehiculo_id)
            await db.execute("UPDATE flota_vehiculos SET odometro_km = GREATEST(odometro_km, $1) WHERE id = $2", data.odometro_km, vehiculo_id)
    return {"status": "success", "id": m_id}


@router.get("/alertas")
async def alertas(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    """Lo que el admin tiene que atender: vencimientos (vencidos o en los proximos 30 dias), services por hacer
    e incidencias abiertas."""
    hoy = date.today()
    lista = []
    for v in await db.fetch(f"SELECT {COLUMNAS} FROM flota_vehiculos WHERE estado != 'baja' ORDER BY patente_identificador"):
        for campo, nombre in (("vtv_vence", "VTV"), ("seguro_vence", "Seguro"), ("habilitacion_vence", "Habilitacion")):
            estado = estado_vencimiento(v[campo], hoy)
            if estado:
                lista.append({"tipo": estado, "que": f"{nombre} de {v['patente_identificador']}", "fecha": v[campo].isoformat()})
        if v['service_cada_km'] > 0:
            faltan = v['ultimo_service_km'] + v['service_cada_km'] - v['odometro_km']
            if faltan <= AVISO_SERVICE_KM:
                lista.append({"tipo": "vencido" if faltan <= 0 else "por_vencer", "que": f"Service de {v['patente_identificador']}",
                              "detalle": f"{'pasado por' if faltan <= 0 else 'faltan'} {abs(faltan):.0f} km"})
    for u in await db.fetch("SELECT nombre_completo, licencia_vence FROM usuarios WHERE activo AND rol IN ('chofer', 'admin') AND licencia_vence IS NOT NULL"):
        estado = estado_vencimiento(u['licencia_vence'], hoy)
        if estado:
            lista.append({"tipo": estado, "que": f"Licencia de {u['nombre_completo']}", "fecha": u['licencia_vence'].isoformat()})
    incidencias = await db.fetchval("SELECT COUNT(*) FROM fletes WHERE estado = 'problema'")
    if incidencias:
        lista.append({"tipo": "incidencia", "que": f"{incidencias} flete(s) con incidencia para reprogramar"})
    return lista
