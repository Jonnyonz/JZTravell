import datetime
import html as html_lib
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import asyncpg

import calculos
from database import get_db, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["Fletes"])


class GPSData(BaseModel):
    lat: float
    lon: float
    precision: Optional[float] = None  # metros (pos.coords.accuracy)


class ConsolidarRequest(BaseModel):
    flete_ids: List[int]
    odometro_salida: Optional[float] = None


class FinalizarRuta(BaseModel):
    odometro_llegada: Optional[float] = None


class EstadoUpdate(BaseModel):
    estado: str


class DocFlete(BaseModel):
    tipo_documento: str
    doc_id: str = ""
    punto_venta: str = ""
    numero: str = ""
    # 'sucursal', 'destinatario' (direccion de un cliente: destino_id = id de la direccion) o
    # 'esporadico' (direccion cargada a mano). En la base, destinatario y esporadico quedan como 'cliente'.
    destino_tipo: str
    destino_id: int = 0
    destino_alias: str = ""
    destino_calle: str = ""
    destino_altura: str = ""
    destino_ciudad: str = ""
    destino_codigo_postal: str = ""
    destino_provincia: str = ""
    destino_lat: Optional[float] = None
    destino_lon: Optional[float] = None
    bultos: int = 1
    volumen_m3: float = 0.0


class FleteCreate(BaseModel):
    origen_id: int
    vehiculo_id: int
    prioridad: str
    distancia_estimada_km: float = 0.0
    documentos: List[DocFlete]
    # Flete externo (servicio a un cliente) cargado directo por el admin, sin cotizacion.
    tipo: str = "interno"
    cliente_id: Optional[int] = None
    precio: Optional[float] = None


# Transiciones de estado permitidas (lista cerrada).
TRANSICIONES_FLETE = {
    "pendiente": {"camino"},
    "camino": {"completado", "problema"},
    "problema": {"pendiente"},
    "completado": set(),
}

# Origen: la sucursal propia o, en un flete externo, la direccion de retiro guardada en el flete.
SELECT_FLETE = """
    SELECT f.id, f.tipo, f.prioridad, f.estado, f.distancia_total_km, f.creado_en, f.chofer_id, f.ruta_id,
           f.cliente_id, cl.razon_social AS cliente, f.precio, v.patente_identificador, v.marca_modelo, v.usa_volumen,
           COALESCE(s.nombre, NULLIF(f.origen_alias, ''), 'Retiro') AS origen,
           COALESCE(s.calle, f.origen_calle) AS origen_calle, COALESCE(s.altura, f.origen_altura) AS origen_altura,
           COALESCE(s.ciudad, f.origen_ciudad) AS origen_ciudad, COALESCE(s.provincia, f.origen_provincia) AS origen_provincia,
           COALESCE(s.latitud, f.origen_lat) AS origen_lat, COALESCE(s.longitud, f.origen_lon) AS origen_lon,
           COALESCE(u.nombre_completo, 'Desconocido') AS solicitante, ch.nombre_completo AS chofer
    FROM fletes f
    LEFT JOIN flota_vehiculos v ON f.vehiculo_id = v.id
    LEFT JOIN sucursales s ON f.origen_id = s.id
    LEFT JOIN usuarios u ON f.solicitante_id = u.id
    LEFT JOIN usuarios ch ON f.chofer_id = ch.id
    LEFT JOIN clientes cl ON f.cliente_id = cl.id
"""


def _filtro_por_rol(user, alias="f"):
    """WHERE y parametros para que cada rol vea solo lo suyo. Admin: todo. Chofer: sus fletes y los pendientes
    sin chofer (disponibles para tomar). Cliente: los de su empresa y los que pidio."""
    if user['rol'] == 'admin':
        return "", []
    if user['rol'] == 'chofer':
        return f"WHERE ({alias}.chofer_id = $1::uuid OR ({alias}.estado = 'pendiente' AND {alias}.chofer_id IS NULL))", [user['id']]
    return f"WHERE ({alias}.solicitante_id = $1::uuid OR ({alias}.cliente_id IS NOT NULL AND {alias}.cliente_id = $2))", [user['id'], user.get('cliente_id')]


async def ubicacion_parada(db, destino_tipo, destino_id, lat=None, lon=None):
    """Coordenadas de una parada: las que vienen, o las de la sucursal o direccion de cliente indicada."""
    if lat is not None and lon is not None:
        return lat, lon
    if destino_id:
        tabla = "sucursales" if destino_tipo == "sucursal" else "direcciones_cliente"
        fila = await db.fetchrow(f"SELECT latitud, longitud FROM {tabla} WHERE id = $1", destino_id)
        if fila:
            return fila["latitud"], fila["longitud"]
    return None, None


@router.get("/tipos_documentos")
async def get_tipos_docs(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    return [dict(r) for r in await db.fetch("SELECT * FROM tipos_documentos WHERE activo=TRUE")]


@router.post("/gps")
async def update_gps(data: GPSData, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    await db.execute("UPDATE usuarios SET lat_actual = $1, lon_actual = $2, ultima_conexion = NOW() WHERE id = $3::uuid", data.lat, data.lon, user['id'])
    # Con una ruta en curso, cada posicion queda en su rastro: con eso se miden los km recorridos.
    ruta_id = await db.fetchval("SELECT id FROM rutas WHERE chofer_id = $1::uuid AND finalizada_en IS NULL ORDER BY id DESC LIMIT 1", user['id'])
    if ruta_id:
        await db.execute("INSERT INTO ruta_gps (ruta_id, lat, lon, precision_m) VALUES ($1, $2, $3, $4)", ruta_id, data.lat, data.lon, data.precision)
    return {"status": "ok", "ruta_id": ruta_id}


# --- DESPACHO DE FLETES ---
@router.get("/fletes")
async def get_fletes(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    where, params = _filtro_por_rol(user)
    fletes = [dict(r) for r in await db.fetch(f"{SELECT_FLETE} {where} ORDER BY f.id DESC LIMIT 100", *params)]
    for f in fletes:
        f['creado_en'] = f['creado_en'].isoformat()
        if f['chofer_id']: f['chofer_id'] = str(f['chofer_id'])
        f['paradas'] = [dict(p) for p in await db.fetch("SELECT * FROM documentos_flete WHERE flete_id = $1 ORDER BY orden_ruta ASC", f['id'])]
    return fletes


@router.post("/fletes")
async def create_flete(data: FleteCreate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    # Validar existencia antes de insertar: si el vehiculo u origen no existen, devolver 400 en vez de un 500.
    vehiculo = await db.fetchrow("SELECT capacidad_volumen_m3, usa_volumen FROM flota_vehiculos WHERE id = $1", data.vehiculo_id)
    if vehiculo is None:
        raise HTTPException(400, "El vehiculo indicado no existe.")
    if not await db.fetchval("SELECT 1 FROM sucursales WHERE id = $1", data.origen_id):
        raise HTTPException(400, "La sucursal de origen no existe.")
    if not data.documentos:
        raise HTTPException(400, "El flete necesita al menos una parada.")
    if data.tipo not in ("interno", "externo"):
        raise HTTPException(400, "Tipo de flete invalido.")
    if data.tipo == "externo" and not (data.cliente_id and await db.fetchval("SELECT 1 FROM clientes WHERE id = $1", data.cliente_id)):
        raise HTTPException(400, "Un flete externo necesita el cliente al que se le presta el servicio.")
    if vehiculo['usa_volumen']:
        v_req = sum(doc.volumen_m3 for doc in data.documentos)
        if v_req > vehiculo['capacidad_volumen_m3']:
            raise HTTPException(400, f"Capacidad volumetrica excedida. Requerido: {v_req} m3 | Disponible: {vehiculo['capacidad_volumen_m3']} m3.")

    async with db.transaction():
        flete_id = await db.fetchval(
            "INSERT INTO fletes (solicitante_id, vehiculo_id, origen_id, prioridad, distancia_total_km, tipo, cliente_id, precio) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
            user['id'], data.vehiculo_id, data.origen_id, data.prioridad, data.distancia_estimada_km, data.tipo,
            data.cliente_id if data.tipo == "externo" else None, data.precio)
        for idx, doc in enumerate(data.documentos):
            if doc.destino_tipo not in ("sucursal", "destinatario", "esporadico", "cliente"):
                raise HTTPException(400, "Tipo de destino invalido.")
            tipo_db = "sucursal" if doc.destino_tipo == "sucursal" else "cliente"
            destino_id = 0 if doc.destino_tipo == "esporadico" else doc.destino_id
            lat, lon = await ubicacion_parada(db, tipo_db, destino_id, doc.destino_lat, doc.destino_lon)
            await db.execute("INSERT INTO documentos_flete (flete_id, tipo_documento, doc_id, punto_venta, numero, destino_tipo, destino_id, destino_alias, destino_calle, destino_altura, destino_ciudad, destino_codigo_postal, destino_provincia, orden_ruta, bultos, volumen_m3, destino_lat, destino_lon) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)", flete_id, doc.tipo_documento, doc.doc_id, doc.punto_venta, doc.numero, tipo_db, destino_id, doc.destino_alias, doc.destino_calle, doc.destino_altura, doc.destino_ciudad, doc.destino_codigo_postal, doc.destino_provincia, idx + 1, doc.bultos, doc.volumen_m3, lat, lon)
    return {"status": "success", "id": flete_id}


@router.post("/fletes/consolidar")
async def consolidar_y_optimizar(data: ConsolidarRequest, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['chofer', 'admin']))):
    """El chofer (o el admin manejando) toma fletes pendientes: se arma una ruta con el orden de entrega,
    los km estimados (linea recta x factor de calle) y el odometro de salida."""
    if not data.flete_ids: raise HTTPException(400, "Lista vacía.")
    if data.odometro_salida is not None and data.odometro_salida < 0:
        raise HTTPException(400, "Odometro invalido.")
    async with db.transaction():
        if await db.fetchval("SELECT 1 FROM rutas WHERE chofer_id = $1::uuid AND finalizada_en IS NULL", user['id']):
            raise HTTPException(400, "Ya tiene una ruta en curso: ciérrela antes de tomar otra.")
        # Solo fletes disponibles (pendientes y sin chofer): un chofer no puede tomar fletes ajenos ni en curso.
        fletes_data = await db.fetch(f"{SELECT_FLETE} WHERE f.id = ANY($1::int[]) AND f.estado = 'pendiente' AND f.chofer_id IS NULL ORDER BY f.id FOR UPDATE OF f", data.flete_ids)
        if len(fletes_data) != len(set(data.flete_ids)):
            raise HTTPException(400, "Alguno de los fletes no esta disponible para consolidar.")
        primero = fletes_data[0]
        origen = {"alias": primero['origen'], "lat": primero['origen_lat'], "lon": primero['origen_lon']}
        paradas = []
        for p in await db.fetch("SELECT id, destino_tipo, destino_id, destino_alias, destino_lat, destino_lon FROM documentos_flete WHERE flete_id = ANY($1::int[])", data.flete_ids):
            lat, lon = await ubicacion_parada(db, p['destino_tipo'], p['destino_id'], p['destino_lat'], p['destino_lon'])
            paradas.append({"id": p['id'], "alias": p['destino_alias'], "lat": lat, "lon": lon})
        orden = calculos.ordenar_paradas(origen, paradas)
        km_linea = round(calculos.km_linea_recta(origen, orden), 2)
        factor = await db.fetchval("SELECT factor_calle FROM configuracion_sistema WHERE id=1") or 1.3
        km_estimados = round(km_linea * factor, 2)
        vehiculo_id = await db.fetchval("SELECT vehiculo_id FROM fletes WHERE id = $1", primero['id'])
        ruta_id = await db.fetchval(
            "INSERT INTO rutas (chofer_id, vehiculo_id, odometro_salida, km_linea_recta, km_estimados) VALUES ($1::uuid, $2, $3, $4, $5) RETURNING id",
            user['id'], vehiculo_id, data.odometro_salida, km_linea, km_estimados)
        await db.execute("UPDATE fletes SET estado = 'camino', chofer_id = $1::uuid, ruta_id = $2 WHERE id = ANY($3::int[])", user['id'], ruta_id, data.flete_ids)
        for idx, p in enumerate(orden):
            await db.execute("UPDATE documentos_flete SET orden_ruta = $1, destino_lat = $2, destino_lon = $3 WHERE id = $4", idx + 1, p['lat'], p['lon'], p['id'])
    return {"status": "success", "ruta_id": ruta_id, "km_estimados": km_estimados,
            "sin_ubicar": calculos.faltan_coordenadas(origen, paradas)}


@router.get("/rutas/actual")
async def ruta_actual(db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    fila = await db.fetchrow("SELECT * FROM rutas WHERE chofer_id = $1::uuid AND finalizada_en IS NULL ORDER BY id DESC LIMIT 1", user['id'])
    if not fila:
        return None
    ruta = dict(fila)
    ruta['chofer_id'] = str(ruta['chofer_id'])
    ruta['iniciada_en'] = ruta['iniciada_en'].isoformat()
    ruta['puntos_gps'] = await db.fetchval("SELECT COUNT(*) FROM ruta_gps WHERE ruta_id = $1", ruta['id'])
    return ruta


@router.post("/rutas/{ruta_id}/finalizar")
async def finalizar_ruta(ruta_id: int, data: FinalizarRuta, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    """Cierra la ruta: completa sus fletes en camino, mide los km por GPS y guarda el odometro de llegada."""
    async with db.transaction():
        ruta = await db.fetchrow("SELECT * FROM rutas WHERE id = $1 FOR UPDATE", ruta_id)
        if not ruta:
            raise HTTPException(404, "Ruta no encontrada.")
        if user['rol'] != 'admin' and str(ruta['chofer_id']) != str(user['id']):
            raise HTTPException(403, "No tiene permiso sobre esta ruta.")
        if ruta['finalizada_en'] is not None:
            raise HTTPException(400, "La ruta ya estaba cerrada.")
        llegada = data.odometro_llegada
        if llegada is not None and ruta['odometro_salida'] is not None and llegada < ruta['odometro_salida']:
            raise HTTPException(400, f"El odometro de llegada no puede ser menor al de salida ({ruta['odometro_salida']:g} km).")
        puntos = await db.fetch("SELECT lat, lon, precision_m, registrado_en FROM ruta_gps WHERE ruta_id = $1 ORDER BY registrado_en, id", ruta_id)
        km_gps = calculos.km_gps([(p['lat'], p['lon'], p['precision_m'], p['registrado_en']) for p in puntos]) if puntos else None
        await db.execute("UPDATE rutas SET finalizada_en = NOW(), odometro_llegada = $1, km_gps = $2 WHERE id = $3", llegada, km_gps, ruta_id)
        await db.execute("UPDATE fletes SET estado = 'completado' WHERE ruta_id = $1 AND estado = 'camino'", ruta_id)
        if llegada is not None and ruta['vehiculo_id']:
            await db.execute("UPDATE flota_vehiculos SET odometro_km = GREATEST(odometro_km, $1) WHERE id = $2", llegada, ruta['vehiculo_id'])
    km_odometro = round(llegada - ruta['odometro_salida'], 2) if llegada is not None and ruta['odometro_salida'] is not None else None
    return {"status": "success", "km_odometro": km_odometro, "km_gps": km_gps, "km_estimados": ruta['km_estimados']}


@router.get("/rutas")
async def listar_rutas(db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    filas = await db.fetch("""
        SELECT r.id, r.iniciada_en, r.finalizada_en, r.odometro_salida, r.odometro_llegada, r.km_linea_recta, r.km_estimados,
               r.km_gps, (r.odometro_llegada - r.odometro_salida) AS km_odometro, u.nombre_completo AS chofer,
               v.patente_identificador, (SELECT COUNT(*) FROM fletes f WHERE f.ruta_id = r.id) AS fletes
        FROM rutas r JOIN usuarios u ON u.id = r.chofer_id LEFT JOIN flota_vehiculos v ON v.id = r.vehiculo_id
        ORDER BY r.id DESC LIMIT 100""")
    rutas = []
    for f in filas:
        r = dict(f)
        r['iniciada_en'] = r['iniciada_en'].isoformat()
        r['finalizada_en'] = r['finalizada_en'].isoformat() if r['finalizada_en'] else None
        rutas.append(r)
    return rutas


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
    if data.estado == 'pendiente':
        # Re-procesar una incidencia: vuelve a quedar disponible para cualquier chofer (antes conservaba el
        # chofer y ninguno lo podia volver a tomar).
        await db.execute("UPDATE fletes SET estado = 'pendiente', chofer_id = NULL, ruta_id = NULL WHERE id = $1", flete_id)
    else:
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

    # Autorizacion por rol: admin ve todo; chofer solo sus fletes; cliente los de su empresa o que pidio.
    # Con IDs secuenciales, sin este control cualquiera enumeraba todos los envios.
    if user['rol'] == 'admin':
        owned = await db.fetch("SELECT id FROM fletes WHERE id = ANY($1::int[])", flete_ids)
    elif user['rol'] == 'chofer':
        owned = await db.fetch("SELECT id FROM fletes WHERE id = ANY($1::int[]) AND chofer_id = $2::uuid", flete_ids, user['id'])
    else:
        owned = await db.fetch("SELECT id FROM fletes WHERE id = ANY($1::int[]) AND (solicitante_id = $2::uuid OR (cliente_id IS NOT NULL AND cliente_id = $3))", flete_ids, user['id'], user.get('cliente_id'))
    if set(flete_ids) - {r['id'] for r in owned}:
        raise HTTPException(403, "No tiene acceso a alguno de los fletes solicitados.")

    cfg = await db.fetchrow("SELECT nombre_empresa FROM configuracion_sistema WHERE id=1")
    nombre_empresa = html_lib.escape(cfg["nombre_empresa"] or "Mi Empresa") if cfg else "Mi Empresa"
    fletes_rows = await db.fetch(f"{SELECT_FLETE} WHERE f.id = ANY($1::int[]) ORDER BY f.id", flete_ids)
    usa_volumen = any(f['usa_volumen'] for f in fletes_rows)
    origen_de = {f['id']: f['origen'] for f in fletes_rows}
    paradas_rows = await db.fetch("SELECT * FROM documentos_flete WHERE flete_id = ANY($1::int[]) ORDER BY orden_ruta ASC", flete_ids)
    fecha_actual = datetime.date.today().strftime("%d/%m/%Y"); codigo_control = str(fletes_rows[0]['id']).zfill(6)

    # Todo dato de la DB (cargado por clientes/choferes) se escapa: sin esto, un campo con
    # <script> quedaba almacenado y se ejecutaba en el navegador del admin al imprimir (XSS).
    def esc(v):
        return html_lib.escape(str(v)) if v is not None else ""

    col_vol_header = '<th style="width: 8%; text-align:center;">Vol (m³)</th>' if usa_volumen else ''

    tabla_filas = ""
    for idx, p in enumerate(paradas_rows):
        bg = "background-color: #ffffff;" if idx % 2 == 0 else "background-color: #f7fafc;"
        col_vol_td = f'<td style="padding:8px; border:1px solid #cbd5e0; text-align:center;">{esc(p["volumen_m3"])} m³</td>' if usa_volumen else ''
        tabla_filas += f'<tr style="{bg}"><td style="padding:8px; border:1px solid #cbd5e0; text-align:center;"><strong>{idx + 1}</strong></td><td style="padding:8px; border:1px solid #cbd5e0;">{esc(p["tipo_documento"].upper())}<br><small style="color:#555;">N° {esc(p["punto_venta"])}-{esc(p["numero"])}</small></td><td style="padding:8px; border:1px solid #cbd5e0;">{esc(origen_de.get(p["flete_id"]))}</td><td style="padding:8px; border:1px solid #cbd5e0; text-align:center;"><strong>{esc(p["bultos"])}</strong></td>{col_vol_td}<td style="padding:8px; border:1px solid #cbd5e0;">{esc(p["destino_alias"])}<br>{esc(p["destino_calle"])} {esc(p["destino_altura"])}, {esc(p["destino_ciudad"])}</td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td><td style="padding:8px; border:1px solid #cbd5e0;"><span style="display:block; border-bottom:1px dotted #a0aec0; height:15px;"></span></td></tr>'

    primero = fletes_rows[0]
    return f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Despacho</title><style>@page {{ size: A4 portrait; margin: 15mm 10mm; }} body {{ font-family: system-ui, sans-serif; color: #333; margin: 0; padding: 0; font-size: 9pt; line-height: 1.4; }} .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }} .title {{ font-size: 18pt; font-weight: bold; text-transform: uppercase; }} .meta-table {{ width: 100%; border-collapse: collapse; background-color: #f8f9fa; border: 1px solid #e2e8f0; margin-bottom: 20px; }} .meta-table td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }} .section-title {{ font-size: 11pt; font-weight: bold; border-bottom: 2px solid #2d3748; padding-bottom: 4px; text-transform: uppercase; margin-bottom: 12px; }} .route-table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; }} .route-table th {{ background-color: #2d3748; color: white; padding: 8px 6px; font-size: 8pt; text-transform: uppercase; text-align: left; }} .signature-table {{ width: 100%; border-collapse: collapse; margin-top: 40px; }} .signature-box {{ width: 45%; text-align: center; }} .signature-space {{ height: 50px; border-bottom: 1px solid #4a5568; margin-bottom: 8px; }} .footer-brand {{ text-align: center; font-size: 7.5pt; color: #888; margin-top: 30px; }} .footer-brand a {{ color: #f06a25; text-decoration: none; font-weight: bold; }} </style></head><body onload="window.print()"><table class="header-table"><tr><td><div class="title">{nombre_empresa}</div><div>Control de Distribución y Logística</div></td><td style="text-align: right; font-size: 8.5pt; color: #555;"><strong>Documento Oficial de Despacho</strong><br>Fecha de Emisión: {fecha_actual}<br><span style="font-size: 18pt; font-weight: bold; color: #000; display: block; margin-top: 5px; letter-spacing: 1px;">N° {codigo_control}</span></td></tr></table><table class="meta-table"><tr><td><strong>Transportista:</strong></td><td>{esc(primero["chofer"] or "No asignado")}</td><td><strong>Vehículo / Patente:</strong></td><td>{esc(primero["marca_modelo"] or "S/D")} ({esc(primero["patente_identificador"] or "S/D")})</td></tr><tr><td><strong>Punto de Salida:</strong></td><td>{esc(primero["origen"])}</td><td><strong>Total Envíos:</strong></td><td>{len(fletes_rows)} Consolidados</td></tr><tr><td style="color: #4a5568; font-weight: bold; border-top: 2px solid #cbd5e0;">Hora de Inicio:</td><td style="border-top: 2px solid #cbd5e0;">______ : ______ hs</td><td style="color: #4a5568; font-weight: bold; border-top: 2px solid #cbd5e0;">Hora de Finalización:</td><td style="border-top: 2px solid #cbd5e0;">______ : ______ hs</td></tr></table><div class="section-title">Secuencia de Entrega Optimizada</div><table class="route-table"><thead><tr><th style="width: 4%; text-align:center;">Sec.</th><th style="width: 15%;">Referencia / Doc.</th><th style="width: 15%;">Origen</th><th style="width: 8%; text-align:center;">Bultos</th>{col_vol_header}<th style="width: 28%;">Dirección de Entrega</th><th style="width: 10%; text-align:center;">Hora Arribo</th><th style="width: 10%; text-align:center;">Hora Salida</th><th style="width: 10%;">Firma Recepción</th></tr></thead><tbody>{tabla_filas}</tbody></table><table class="signature-table"><tr><td class="signature-box"><div class="signature-space"></div><div>Firma del Transportista</div></td><td style="width: 10%;"></td><td class="signature-box"><div class="signature-space"></div><div>Control de Despacho Central</div></td></tr></table><div class="footer-brand">Powered by <a href="https://jz-tech.mywire.org" target="_blank">JZ Tech Solutions</a></div></body></html>'
