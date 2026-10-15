from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from calculos import factor_sugerido
from database import get_db, require_role

router = APIRouter(prefix="/api", tags=["Configuracion"])


class ConfigUpdate(BaseModel):
    nombre_empresa: str
    gps_polling_sec: int
    google_oauth_enabled: bool
    google_client_id: Optional[str] = None
    # Vacio = se conserva el guardado (el GET nunca lo devuelve, asi que el panel no lo puede reenviar).
    google_client_secret: Optional[str] = None
    google_redirect_url: Optional[str] = None
    ciudad_defecto: str
    provincia_defecto: str
    pais_defecto: str


class CostosUpdate(BaseModel):
    precio_litro: float
    precio_kwh: float
    costo_chofer_hora: float
    velocidad_promedio_kmh: float
    minutos_por_parada: float
    margen_pct: float
    iva_pct: float
    factor_calle: float


COSTOS = "precio_litro, precio_kwh, costo_chofer_hora, velocidad_promedio_kmh, minutos_por_parada, margen_pct, iva_pct, factor_calle"


# Publico (lo usa index.html antes del login): sin secretos ni costos.
@router.get("/config")
async def get_config(db: asyncpg.Connection = Depends(get_db)):
    return dict(await db.fetchrow("SELECT nombre_empresa, gps_polling_sec, google_oauth_enabled, google_client_id, google_redirect_url, ciudad_defecto, provincia_defecto, pais_defecto FROM configuracion_sistema WHERE id=1"))


@router.post("/config")
async def update_config(data: ConfigUpdate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("""
        UPDATE configuracion_sistema SET nombre_empresa=$1, gps_polling_sec=$2, google_oauth_enabled=$3, google_client_id=$4,
        google_client_secret=COALESCE(NULLIF($5, ''), google_client_secret), google_redirect_url=$6,
        ciudad_defecto=$7, provincia_defecto=$8, pais_defecto=$9 WHERE id=1
    """, data.nombre_empresa, data.gps_polling_sec, data.google_oauth_enabled, data.google_client_id, data.google_client_secret, data.google_redirect_url, data.ciudad_defecto, data.provincia_defecto, data.pais_defecto)
    return {"status": "success"}


async def _factor_sugerido(db):
    """Factor calle segun los ultimos 30 viajes terminados: km reales (odometro, o GPS si falta) / km en linea recta."""
    filas = await db.fetch("""
        SELECT COALESCE(odometro_llegada - odometro_salida, km_gps) AS reales, km_linea_recta AS linea
        FROM rutas WHERE finalizada_en IS NOT NULL ORDER BY finalizada_en DESC LIMIT 30""")
    viajes = [(f["reales"], f["linea"]) for f in filas]
    return factor_sugerido(viajes), len([v for v in viajes if v[0] and v[1] and v[1] >= 2])


@router.get("/config/costos")
async def get_costos(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    costos = dict(await db.fetchrow(f"SELECT {COSTOS} FROM configuracion_sistema WHERE id=1"))
    costos["factor_sugerido"], costos["viajes_medidos"] = await _factor_sugerido(db)
    return costos


@router.post("/config/costos")
async def update_costos(data: CostosUpdate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    valores = data.model_dump()
    if min(valores.values()) < 0:
        raise HTTPException(400, "Los valores no pueden ser negativos.")
    if data.velocidad_promedio_kmh <= 0:
        raise HTTPException(400, "La velocidad promedio tiene que ser mayor a 0.")
    if not 1 <= data.factor_calle <= 3:
        raise HTTPException(400, "El factor de calle va de 1 a 3 (1,3 es lo habitual en ciudad).")
    await db.execute("""
        UPDATE configuracion_sistema SET precio_litro=$1, precio_kwh=$2, costo_chofer_hora=$3, velocidad_promedio_kmh=$4,
        minutos_por_parada=$5, margen_pct=$6, iva_pct=$7, factor_calle=$8 WHERE id=1
    """, data.precio_litro, data.precio_kwh, data.costo_chofer_hora, data.velocidad_promedio_kmh, data.minutos_por_parada, data.margen_pct, data.iva_pct, data.factor_calle)
    return {"status": "success"}
