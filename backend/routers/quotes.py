"""Cotizaciones de viajes para clientes externos.

Flujo: el cliente la pide desde su portal (o el admin la arma) -> 'solicitada'; el admin elige vehiculo y
ajusta km, peajes y otros gastos, y la app calcula el desglose (calculos.costo_cotizacion) -> la envia
('enviada'); el cliente (o el admin por el) la acepta -> se crea el flete externo, o la rechaza.
"""
import json
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

import calculos
from database import get_db, require_role

router = APIRouter(prefix="/api", tags=["Cotizaciones"])

# Desglose interno: el cliente no lo ve (con subtotal y total se deduciria el margen).
INTERNOS = ("costo_energia", "costo_desgaste", "costo_chofer", "otros_gastos", "subtotal", "margen", "margen_pct", "notas")


class Punto(BaseModel):
    alias: str = ""
    calle: str = ""
    altura: str = ""
    ciudad: str = ""
    provincia: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None


class Parada(Punto):
    bultos: int = 1
    volumen_m3: float = 0.0


class CotizacionCreate(BaseModel):
    cliente_id: Optional[int] = None  # el admin indica para que cliente; un usuario cliente usa su empresa
    descripcion: str = ""
    fecha_viaje: Optional[date] = None
    origen: Punto
    paradas: List[Parada]


class CotizacionCalculo(BaseModel):
    vehiculo_id: int
    km_estimados: Optional[float] = None  # None = linea recta x factor de calle
    peajes: float = 0.0
    otros_gastos: float = 0.0
    margen_pct: Optional[float] = None  # None = el de la configuracion
    iva_pct: Optional[float] = None
    notas: str = ""


COSTOS_CFG = "precio_litro, precio_kwh, costo_chofer_hora, velocidad_promedio_kmh, minutos_por_parada, margen_pct, iva_pct, factor_calle"


def _a_dict(fila):
    c = dict(fila)
    for k in ("origen", "paradas"):
        if isinstance(c.get(k), str):
            c[k] = json.loads(c[k])
    for k in ("creado_en", "actualizado_en", "fecha_viaje"):
        if c.get(k) is not None:
            c[k] = c[k].isoformat()
    if c.get("solicitante_id"):
        c["solicitante_id"] = str(c["solicitante_id"])
    return c


async def _cotizacion_visible(db, cotizacion_id: int, user):
    fila = await db.fetchrow("SELECT * FROM cotizaciones WHERE id = $1", cotizacion_id)
    if not fila:
        raise HTTPException(404, "Cotizacion no encontrada.")
    if user['rol'] == 'admin':
        return fila
    es_suya = user['rol'] == 'cliente' and (
        str(fila['solicitante_id']) == str(user['id']) or (fila['cliente_id'] is not None and fila['cliente_id'] == user.get('cliente_id')))
    if not es_suya:
        raise HTTPException(404, "Cotizacion no encontrada.")
    return fila


def _km_sugeridos(c, factor):
    origen = c["origen"] or {}
    paradas = c["paradas"] or []
    orden = calculos.ordenar_paradas(origen, paradas)
    return round(calculos.km_linea_recta(origen, orden) * factor, 2), calculos.faltan_coordenadas(origen, paradas)


@router.get("/cotizaciones")
async def listar_cotizaciones(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin', 'cliente']))):
    base = "SELECT c.*, cl.razon_social AS cliente, v.patente_identificador FROM cotizaciones c LEFT JOIN clientes cl ON cl.id = c.cliente_id LEFT JOIN flota_vehiculos v ON v.id = c.vehiculo_id"
    if user['rol'] == 'admin':
        filas = await db.fetch(f"{base} ORDER BY c.id DESC LIMIT 200")
    else:
        filas = await db.fetch(f"{base} WHERE c.solicitante_id = $1::uuid OR (c.cliente_id IS NOT NULL AND c.cliente_id = $2) ORDER BY c.id DESC LIMIT 200", user['id'], user.get('cliente_id'))
    factor = await db.fetchval("SELECT factor_calle FROM configuracion_sistema WHERE id=1") or 1.3
    cotizaciones = []
    for f in filas:
        c = _a_dict(f)
        if user['rol'] == 'admin':
            c["km_sugeridos"], c["sin_ubicar"] = _km_sugeridos(c, factor)
        else:
            for k in INTERNOS:
                c.pop(k, None)
            if c["estado"] == "solicitada":
                # Mientras el admin la arma, el cliente no ve importes a medio calcular.
                c["total"] = c["iva"] = None
        cotizaciones.append(c)
    return cotizaciones


@router.post("/cotizaciones")
async def crear_cotizacion(data: CotizacionCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin', 'cliente']))):
    if not data.paradas:
        raise HTTPException(400, "Indique al menos un destino.")
    if user['rol'] == 'cliente':
        cliente_id = user.get('cliente_id')
        if not cliente_id:
            raise HTTPException(400, "Su usuario no esta vinculado a una empresa: pida al administrador que lo vincule.")
    else:
        cliente_id = data.cliente_id
        if not cliente_id or not await db.fetchval("SELECT 1 FROM clientes WHERE id = $1", cliente_id):
            raise HTTPException(400, "Indique el cliente de la cotizacion.")
    c_id = await db.fetchval(
        "INSERT INTO cotizaciones (cliente_id, solicitante_id, descripcion, fecha_viaje, origen, paradas) VALUES ($1, $2::uuid, $3, $4, $5::jsonb, $6::jsonb) RETURNING id",
        cliente_id, user['id'], data.descripcion, data.fecha_viaje,
        json.dumps(data.origen.model_dump()), json.dumps([p.model_dump() for p in data.paradas]))
    return {"status": "success", "id": c_id}


@router.put("/cotizaciones/{cotizacion_id}")
async def calcular_cotizacion(cotizacion_id: int, data: CotizacionCalculo, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    fila = await _cotizacion_visible(db, cotizacion_id, user)
    if fila['estado'] not in ('solicitada', 'enviada'):
        raise HTTPException(400, "Solo se pueden recalcular cotizaciones solicitadas o enviadas.")
    vehiculo = await db.fetchrow("SELECT tipo, consumo_100km, costo_km, usa_volumen, capacidad_volumen_m3 FROM flota_vehiculos WHERE id = $1", data.vehiculo_id)
    if not vehiculo:
        raise HTTPException(400, "El vehiculo indicado no existe.")
    if min(data.peajes, data.otros_gastos, data.km_estimados or 0) < 0:
        raise HTTPException(400, "Los valores no pueden ser negativos.")
    c = _a_dict(fila)
    if vehiculo['usa_volumen']:
        volumen = sum(p.get('volumen_m3') or 0 for p in c['paradas'])
        if volumen > vehiculo['capacidad_volumen_m3']:
            raise HTTPException(400, f"Capacidad volumetrica excedida. Requerido: {volumen} m3 | Disponible: {vehiculo['capacidad_volumen_m3']} m3.")
    cfg = dict(await db.fetchrow(f"SELECT {COSTOS_CFG} FROM configuracion_sistema WHERE id=1"))
    km = data.km_estimados if data.km_estimados is not None else _km_sugeridos(c, cfg["factor_calle"])[0]
    d = calculos.costo_cotizacion(km, len(c['paradas']), dict(vehiculo), cfg, data.peajes, data.otros_gastos, data.margen_pct, data.iva_pct)
    await db.execute("""
        UPDATE cotizaciones SET vehiculo_id=$1, km_estimados=$2, horas_estimadas=$3, peajes=$4, otros_gastos=$5, costo_energia=$6,
        costo_desgaste=$7, costo_chofer=$8, subtotal=$9, margen_pct=$10, margen=$11, iva_pct=$12, iva=$13, total=$14, notas=$15,
        actualizado_en=NOW() WHERE id=$16
    """, data.vehiculo_id, d["km_estimados"], d["horas_estimadas"], d["peajes"], d["otros_gastos"], d["costo_energia"],
        d["costo_desgaste"], d["costo_chofer"], d["subtotal"], d["margen_pct"], d["margen"], d["iva_pct"], d["iva"], d["total"],
        data.notas, cotizacion_id)
    return {"status": "success", **d}


@router.post("/cotizaciones/{cotizacion_id}/enviar")
async def enviar_cotizacion(cotizacion_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    fila = await _cotizacion_visible(db, cotizacion_id, user)
    if fila['estado'] != 'solicitada':
        raise HTTPException(400, "Solo se envian cotizaciones solicitadas.")
    if not fila['vehiculo_id'] or not fila['total']:
        raise HTTPException(400, "Calcule la cotizacion (vehiculo y km) antes de enviarla.")
    await db.execute("UPDATE cotizaciones SET estado='enviada', actualizado_en=NOW() WHERE id=$1", cotizacion_id)
    return {"status": "success"}


@router.post("/cotizaciones/{cotizacion_id}/rechazar")
async def rechazar_cotizacion(cotizacion_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin', 'cliente']))):
    fila = await _cotizacion_visible(db, cotizacion_id, user)
    if fila['estado'] not in ('solicitada', 'enviada'):
        raise HTTPException(400, "La cotizacion ya fue resuelta.")
    await db.execute("UPDATE cotizaciones SET estado='rechazada', actualizado_en=NOW() WHERE id=$1", cotizacion_id)
    return {"status": "success"}


@router.post("/cotizaciones/{cotizacion_id}/aceptar")
async def aceptar_cotizacion(cotizacion_id: int, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin', 'cliente']))):
    """Acepta una cotizacion enviada y crea el flete externo (pendiente, listo para que lo tome un chofer)."""
    async with db.transaction():
        await _cotizacion_visible(db, cotizacion_id, user)
        fila = await db.fetchrow("SELECT * FROM cotizaciones WHERE id = $1 FOR UPDATE", cotizacion_id)
        if fila['estado'] != 'enviada':
            raise HTTPException(400, "Solo se aceptan cotizaciones enviadas.")
        c = _a_dict(fila)
        o = c['origen'] or {}
        flete_id = await db.fetchval("""
            INSERT INTO fletes (solicitante_id, vehiculo_id, origen_id, prioridad, distancia_total_km, tipo, cliente_id, precio,
                                origen_alias, origen_calle, origen_altura, origen_ciudad, origen_provincia, origen_lat, origen_lon)
            VALUES ($1::uuid, $2, NULL, 'normal', $3, 'externo', $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING id
        """, fila['solicitante_id'] or user['id'], fila['vehiculo_id'], fila['km_estimados'], fila['cliente_id'], fila['total'],
            o.get('alias') or 'Retiro', o.get('calle', ''), o.get('altura', ''), o.get('ciudad', ''), o.get('provincia', ''),
            o.get('lat'), o.get('lon'))
        for idx, p in enumerate(c['paradas']):
            await db.execute("""
                INSERT INTO documentos_flete (flete_id, tipo_documento, numero, destino_tipo, destino_id, destino_alias, destino_calle,
                    destino_altura, destino_ciudad, destino_provincia, orden_ruta, bultos, volumen_m3, destino_lat, destino_lon)
                VALUES ($1, 'cotizacion', $2, 'cliente', 0, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """, flete_id, str(cotizacion_id), p.get('alias', ''), p.get('calle', ''), p.get('altura', ''), p.get('ciudad', ''),
                p.get('provincia', ''), idx + 1, p.get('bultos') or 1, p.get('volumen_m3') or 0, p.get('lat'), p.get('lon'))
        await db.execute("UPDATE cotizaciones SET estado='aceptada', flete_id=$1, actualizado_en=NOW() WHERE id=$2", flete_id, cotizacion_id)
    return {"status": "success", "flete_id": flete_id}
