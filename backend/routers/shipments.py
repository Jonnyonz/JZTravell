import math
import datetime
import html as html_lib
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Fletes"])


class GPSData(BaseModel):
    lat: float
    lon: float


class ConsolidarRequest(BaseModel):
    flete_ids: List[int]


class EstadoUpdate(BaseModel):
    estado: str


class DocFlete(BaseModel):
    tipo_documento: str
    doc_id: str = ""
    punto_venta: str = ""
    numero: str = ""
    destino_tipo: str
    destino_id: int = 0
    destino_alias: str = ""
    destino_calle: str = ""
    destino_altura: str = ""
    destino_ciudad: str = ""
    destino_codigo_postal: str = ""
    destino_provincia: str = ""
    bultos: int = 1
    volumen_m3: float = 0.0


class FleteCreate(BaseModel):
    origen_id: int
    vehiculo_id: int
    prioridad: str
    distancia_estimada_km: float = 0.0
    documentos: List[DocFlete]


# --- AUXILIARES MATEMÁTICOS (solo los usa consolidar_y_optimizar) ---
def calcular_distancia_haversine(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2): return 0.0
    rad = math.pi / 180
    dlat = (lat2 - lat1) * rad; dlon = (lon2 - lon1) * rad
    a = math.sin(dlat/2)**2 + math.cos(lat1*rad) * math.cos(lat2*rad) * math.sin(dlon/2)**2
    return 6371.0 * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))


def optimizar_secuencia_paradas(origen_lat, origen_lon, paradas):
    if not paradas: return []
    ruta_optimizada = []; pendientes = list(paradas)
    lat_actual, lon_actual = origen_lat, origen_lon
    while pendientes:
        proxima = min(pendientes, key=lambda p: calcular_distancia_haversine(lat_actual, lon_actual, p.get('latitud', -34.6037), p.get('longitud', -58.3816)))
        pendientes.remove(proxima)
        ruta_optimizada.append(proxima)
        lat_actual, lon_actual = proxima.get('latitud', -34.6037), proxima.get('longitud', -58.3816)
    return ruta_optimizada


# Transiciones de estado permitidas (lista cerrada).
TRANSICIONES_FLETE = {
    "pendiente": {"camino"},
    "camino": {"completado", "problema"},
    "problema": {"pendiente"},
    "completado": set(),
}


@router.get("/tipos_documentos")
async def get_tipos_docs(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    return [dict(r) for r in await db.fetch("SELECT * FROM tipos_documentos WHERE activo=TRUE")]


@router.post("/gps")
async def update_gps(data: GPSData, request: Request, db: asyncpg.Connection = Depends(get_db)):
    user = await get_current_user(request, db)
    await db.execute("UPDATE usuarios SET lat_actual = $1, lon_actual = $2, ultima_conexion = NOW() WHERE id = $3::uuid", data.lat, data.lon, user['id'])
    return {"status": "ok"}


# --- DESPACHO DE FLETES ---
@router.get("/fletes")
async def get_fletes(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    # Autorizacion por rol: admin ve todo; chofer sus fletes + los pendientes (disponibles para
    # tomar); cliente solo los que solicito. Antes devolvia todos a cualquier rol.
    if user['rol'] == 'admin':
        where, params = "", []
    elif user['rol'] == 'chofer':
        where, params = "WHERE (f.chofer_id = $1::uuid OR f.estado = 'pendiente')", [user['id']]
    else:
        where, params = "WHERE f.solicitante_id = $1::uuid", [user['id']]
    query = f"SELECT f.id, f.prioridad, f.estado, f.distancia_total_km, f.creado_en, f.chofer_id, v.patente_identificador, v.marca_modelo, s.nombre as origen, s.calle as origen_calle, s.altura as origen_altura, s.ciudad as origen_ciudad, s.provincia as origen_provincia, s.latitud as origen_lat, s.longitud as origen_lon, COALESCE(u.nombre_completo, 'Desconocido') as solicitante FROM fletes f LEFT JOIN flota_vehiculos v ON f.vehiculo_id = v.id LEFT JOIN sucursales s ON f.origen_id = s.id LEFT JOIN usuarios u ON f.solicitante_id = u.id {where} ORDER BY f.id DESC LIMIT 100"
    fletes = [dict(r) for r in await db.fetch(query, *params)]
    for f in fletes:
        f['creado_en'] = f['creado_en'].isoformat()
        if f['chofer_id']: f['chofer_id'] = str(f['chofer_id'])
        f['paradas'] = [dict(p) for p in await db.fetch("SELECT * FROM documentos_flete WHERE flete_id = $1 ORDER BY orden_ruta ASC", f['id'])]
    return fletes


@router.post("/fletes")
async def create_flete(data: FleteCreate, request: Request, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    cfg = await db.fetchrow("SELECT manejar_volumenes FROM configuracion_sistema WHERE id=1")
    # Validar existencia antes de insertar: si el vehiculo u origen no existen, devolver 400
    # en vez de un 500 (antes v_cap era None y explotaba, o saltaba la violacion de FK).
    v_cap = await db.fetchval("SELECT capacidad_volumen_m3 FROM flota_vehiculos WHERE id = $1", data.vehiculo_id)
    if v_cap is None:
        raise HTTPException(400, "El vehiculo indicado no existe.")
    if not await db.fetchval("SELECT 1 FROM sucursales WHERE id = $1", data.origen_id):
        raise HTTPException(400, "La sucursal de origen no existe.")
    if cfg['manejar_volumenes']:
        v_req = sum(doc.volumen_m3 for doc in data.documentos)
        if v_req > v_cap: raise HTTPException(400, f"Capacidad volumetrica excedida. Requerido: {v_req} m3 | Disponible: {v_cap} m3.")

    async with db.transaction():
        flete_id = await db.fetchval("INSERT INTO fletes (solicitante_id, vehiculo_id, origen_id, prioridad, distancia_total_km) VALUES ($1::uuid, $2, $3, $4, $5) RETURNING id", user['id'], data.vehiculo_id, data.origen_id, data.prioridad, data.distancia_estimada_km)
        for idx, doc in enumerate(data.documentos):
            await db.execute("INSERT INTO documentos_flete (flete_id, tipo_documento, doc_id, punto_venta, numero, destino_tipo, destino_id, destino_alias, destino_calle, destino_altura, destino_ciudad, destino_codigo_postal, destino_provincia, orden_ruta, bultos, volumen_m3) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)", flete_id, doc.tipo_documento, doc.doc_id, doc.punto_venta, doc.numero, doc.destino_tipo, doc.destino_id, doc.destino_alias, doc.destino_calle, doc.destino_altura, doc.destino_ciudad, doc.destino_codigo_postal, doc.destino_provincia, idx + 1, doc.bultos, doc.volumen_m3)
    return {"status": "success"}


@router.post("/fletes/consolidar")
async def consolidar_y_optimizar(data: ConsolidarRequest, request: Request, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['chofer']))):
    if not data.flete_ids: raise HTTPException(400, "Lista vacía.")
    async with db.transaction():
        # Solo se pueden consolidar fletes disponibles (pendientes y sin chofer): asi un chofer
        # no puede tomar fletes ajenos ni ya en curso.
        fletes_data = await db.fetch("SELECT f.id, s.latitud, s.longitud FROM fletes f JOIN sucursales s ON f.origen_id = s.id WHERE f.id = ANY($1::int[]) AND f.estado = 'pendiente' AND f.chofer_id IS NULL", data.flete_ids)
        if len(fletes_data) != len(set(data.flete_ids)):
            raise HTTPException(400, "Alguno de los fletes no esta disponible para consolidar.")
        orig_lat, orig_lon = fletes_data[0]['latitud'], fletes_data[0]['longitud']
        paradas = [dict(p) for p in await db.fetch("SELECT * FROM documentos_flete WHERE flete_id = ANY($1::int[])", data.flete_ids)]
        for p in paradas:
            coords = await db.fetchrow("SELECT latitud, longitud FROM sucursales WHERE id = $1" if p['destino_tipo'] == 'sucursal' else "SELECT latitud, longitud FROM direcciones_cliente WHERE id = $1", p['destino_id'])
            p['latitud'] = coords['latitud'] if coords else -34.6037; p['longitud'] = coords['longitud'] if coords else -58.3816
        paradas_ordenadas = optimizar_secuencia_paradas(orig_lat, orig_lon, paradas)
        await db.execute("UPDATE fletes SET estado = 'camino', chofer_id = $1::uuid WHERE id = ANY($2::int[])", user['id'], data.flete_ids)
        for idx, p in enumerate(paradas_ordenadas): await db.execute("UPDATE documentos_flete SET orden_ruta = $1 WHERE id = $2", idx + 1, p['id'])
    return {"status": "success"}


@router.post("/fletes/{flete_id}/estado")
async def update_flete_estado(flete_id: int, data: EstadoUpdate, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    flete = await db.fetchrow("SELECT estado, chofer_id FROM fletes WHERE id = $1", flete_id)
    if not flete:
        raise HTTPException(404, "Flete no encontrado.")
    # Solo el admin o el chofer asignado a ese flete pueden cambiar su estado.
    es_chofer_asignado = user['rol'] == 'chofer' and flete['chofer_id'] is not None and str(flete['chofer_id']) == str(user['id'])
    if user['rol'] != 'admin' and not es_chofer_asignado:
        raise HTTPException(403, "No tiene permiso sobre este flete.")
    if data.estado not in TRANSICIONES_FLETE:
        raise HTTPException(400, "Estado invalido.")
    if data.estado not in TRANSICIONES_FLETE.get(flete['estado'], set()):
        raise HTTPException(400, f"Transicion no permitida desde '{flete['estado']}'.")
    await db.execute("UPDATE fletes SET estado = $1 WHERE id = $2", data.estado, flete_id)
    return {"status": "success"}


# --- HOJA DE IMPRESIÓN ---
@router.get("/fletes/imprimir", response_class=HTMLResponse)
async def imprimir_hoja_ruta(ids: str, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    try:
        flete_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "Identificadores de flete invalidos.")
    if not flete_ids:
        raise HTTPException(400, "No se indicaron fletes para imprimir.")

    # Autorizacion por rol: admin ve todo; chofer solo sus fletes; cliente solo los que solicito.
    # Con IDs secuenciales, sin este control cualquiera enumeraba todos los envios.
    if user['rol'] == 'admin':
        owned = await db.fetch("SELECT id FROM fletes WHERE id = ANY($1::int[])", flete_ids)
    elif user['rol'] == 'chofer':
        owned = await db.fetch("SELECT id FROM fletes WHERE id = ANY($1::int[]) AND chofer_id = $2::uuid", flete_ids, user['id'])
    else:
        owned = await db.fetch("SELECT id FROM fletes WHERE id = ANY($1::int[]) AND solicitante_id = $2::uuid", flete_ids, user['id'])
    if set(flete_ids) - {r['id'] for r in owned}:
        raise HTTPException(403, "No tiene acceso a alguno de los fletes solicitados.")

    cfg = await db.fetchrow("SELECT manejar_volumenes, nombre_empresa FROM configuracion_sistema WHERE id=1")
    nombre_empresa = html_lib.escape(cfg["nombre_empresa"] or "Mi Empresa") if cfg else "Mi Empresa"
    fletes_rows = await db.fetch("SELECT f.id, s.nombre as origen, u.nombre_completo as chofer, v.patente_identificador, v.marca_modelo FROM fletes f LEFT JOIN sucursales s ON f.origen_id = s.id LEFT JOIN usuarios u ON f.chofer_id = u.id LEFT JOIN flota_vehiculos v ON f.vehiculo_id = v.id WHERE f.id = ANY($1::int[])", flete_ids)
    paradas_rows = await db.fetch("SELECT d.*, s.nombre as sucursal_origen FROM documentos_flete d JOIN fletes f ON d.flete_id = f.id JOIN sucursales s ON f.origen_id = s.id WHERE d.flete_id = ANY($1::int[]) ORDER BY d.orden_ruta ASC", flete_ids)
    fecha_actual = datetime.date.today().strftime("%d/%m/%Y"); codigo_control = str(fletes_rows[0]['id']).zfill(6)

    # Todo dato de la DB (cargado por clientes/choferes) se escapa: sin esto, un campo con
    # <script> quedaba almacenado y se ejecutaba en el navegador del admin al imprimir (XSS).
    def esc(v):
        return html_lib.escape(str(v)) if v is not None else ""

    col_vol_header = '<th style="width: 8%; text-align:center;">Vol (m³)</th>' if cfg['manejar_volumenes'] else ''

    tabla_filas = ""
    for idx, p in enumerate(paradas_rows):
        bg = "background-color: #ffffff;" if idx % 2 == 0 else "background-color: #f7fafc;"
        col_vol_td = f'<td style="padding:8px; border:1px solid #cbd5e0; text-align:center;">{esc(p["volumen_m3"])} m³</td>' if cfg['manejar_volumenes'] else ''
        tabla_filas += f'<tr style="{bg}"><td style="padding:8px; border:1px solid #cbd5e0; text-align:center;"><strong>{idx + 1}</strong></td><td style="padding:8px; border:1px solid #cbd5e0;">{esc(p["tipo_documento"].upper())}<br><small style="color:#555;">N° {esc(p["punto_venta"])}-{esc(p["numero"])}</small></td><td style="padding:8px; border:1px solid #cbd5e0;">{esc(p["sucursal_origen"])}</td><td style="padding:8px; border:1px solid #cbd5e0; text-align:center;"><strong>{esc(p["bultos"])}</strong></td>{col_vol_td}<td style="padding:8px; border:1px solid #cbd5e0;">{esc(p["destino_calle"])} {esc(p["destino_altura"])}, {esc(p["destino_ciudad"])}</td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td></tr>'

    return f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Despacho</title><style>@page {{ size: A4 portrait; margin: 15mm 10mm; }} body {{ font-family: system-ui, sans-serif; color: #333; margin: 0; padding: 0; font-size: 9pt; line-height: 1.4; }} .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }} .title {{ font-size: 18pt; font-weight: bold; text-transform: uppercase; }} .meta-table {{ width: 100%; border-collapse: collapse; background-color: #f8f9fa; border: 1px solid #e2e8f0; margin-bottom: 20px; }} .meta-table td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }} .section-title {{ font-size: 11pt; font-weight: bold; border-bottom: 2px solid #2d3748; padding-bottom: 4px; text-transform: uppercase; margin-bottom: 12px; }} .route-table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; }} .route-table th {{ background-color: #2d3748; color: white; padding: 8px 6px; font-size: 8pt; text-transform: uppercase; text-align: left; }} .signature-table {{ width: 100%; border-collapse: collapse; margin-top: 40px; }} .signature-box {{ width: 45%; text-align: center; }} .signature-space {{ height: 50px; border-bottom: 1px solid #4a5568; margin-bottom: 8px; }} .footer-brand {{ text-align: center; font-size: 7.5pt; color: #888; margin-top: 30px; }} .footer-brand a {{ color: #f06a25; text-decoration: none; font-weight: bold; }} </style></head><body onload="window.print()"><table class="header-table"><tr><td><div class="title">{nombre_empresa}</div><div>Control de Distribución y Logística</div></td><td style="text-align: right; font-size: 8.5pt; color: #555;"><strong>Documento Oficial de Despacho</strong><br>Fecha de Emisión: {fecha_actual}<br><span style="font-size: 18pt; font-weight: bold; color: #000; display: block; margin-top: 5px; letter-spacing: 1px;">N° {codigo_control}</span></td></tr></table><table class="meta-table"><tr><td><strong>Transportista:</strong></td><td>{esc(fletes_rows[0]["chofer"] or "No asignado")}</td><td><strong>Vehículo / Patente:</strong></td><td>{esc(fletes_rows[0]["marca_modelo"] or "S/D")} ({esc(fletes_rows[0]["patente_identificador"] or "S/D")})</td></tr><tr><td><strong>Punto de Salida:</strong></td><td>{esc(fletes_rows[0]["origen"])}</td><td><strong>Total Envíos:</strong></td><td>{len(fletes_rows)} Consolizados</td></tr><tr><td style="color: #4a5568; font-weight: bold; border-top: 2px solid #cbd5e0;">Hora de Inicio:</td><td style="border-top: 2px solid #cbd5e0;">______ : ______ hs</td><td style="color: #4a5568; font-weight: bold; border-top: 2px solid #cbd5e0;">Hora de Finalización:</td><td style="border-top: 2px solid #cbd5e0;">______ : ______ hs</td></tr></table><div class="section-title">Secuencia de Entrega Optimizada</div><table class="route-table"><thead><tr><th style="width: 4%; text-align:center;">Sec.</th><th style="width: 15%;">Referencia / Doc.</th><th style="width: 15%;">Origen</th><th style="width: 8%; text-align:center;">Bultos</th>{col_vol_header}<th style="width: 28%;">Dirección de Entrega</th><th style="width: 10%; text-align:center;">Hora Arribo</th><th style="width: 10%; text-align:center;">Hora Salida</th><th style="width: 10%;">Firma Recepción</th></tr></thead><tbody>{tabla_filas}</tbody></table><table class="signature-table"><tr><td class="signature-box"><div class="signature-space"></div><div>Firma del Transportista</div></td><td style="width: 10%;"></td><td class="signature-box"><div class="signature-space"></div><div>Control de Despacho Central</div></td></tr></table><div class="footer-brand">Versión 1.9<br>Powered by <a href="https://jz-tech.mywire.org" target="_blank">JZ Tech Solutions</a></div></body></html>'
