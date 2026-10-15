"""Prueba de entrega (por parada) y link de seguimiento publico.

El chofer marca cada parada como entregada (quien recibio, firma y foto opcionales) o no entregada (motivo:
incidencia). Se guarda su posicion GPS; si la direccion no tenia ubicacion, se completa con esa posicion.
"""
import base64
import binascii
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import asyncpg

from database import get_db, get_current_user

router = APIRouter(prefix="/api", tags=["Entregas"])

MAX_IMAGEN = 1_500_000  # bytes por imagen (el celular las achica antes de mandarlas)
MOTIVOS = ("ausente", "rechazo", "direccion_erronea", "danado", "cerrado", "otro")
FIRMAS = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg"}


class EntregaData(BaseModel):
    estado: str  # 'entregada' | 'no_entregada'
    recibido_por: str = ""
    motivo: str = ""
    observaciones: str = ""
    firma: Optional[str] = None  # data URL o base64 (PNG)
    foto: Optional[str] = None   # data URL o base64 (JPEG)
    lat: Optional[float] = None
    lon: Optional[float] = None


def _imagen(dato: Optional[str], nombre: str) -> Optional[bytes]:
    if not dato:
        return None
    if dato.startswith("data:"):
        dato = dato.split(",", 1)[-1]
    try:
        crudo = base64.b64decode(dato, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, f"La {nombre} no es una imagen valida.")
    if len(crudo) > MAX_IMAGEN:
        raise HTTPException(400, f"La {nombre} es demasiado grande.")
    if not any(crudo.startswith(m) for m in FIRMAS):
        raise HTTPException(400, f"La {nombre} tiene que ser PNG o JPEG.")
    return crudo


def _tipo(crudo: bytes) -> str:
    return next(t for m, t in FIRMAS.items() if crudo.startswith(m))


async def _parada_del_usuario(db, documento_id: int, user):
    """Parada + flete. Admin: cualquiera; chofer: las de sus fletes; cliente: las de su empresa o que pidio."""
    p = await db.fetchrow("""
        SELECT d.id, d.flete_id, d.destino_tipo, d.destino_id, d.destino_lat, d.destino_lon,
               f.estado AS flete_estado, f.chofer_id, f.solicitante_id, f.cliente_id
        FROM documentos_flete d JOIN fletes f ON f.id = d.flete_id WHERE d.id = $1""", documento_id)
    if not p:
        raise HTTPException(404, "Parada no encontrada.")
    if user['rol'] == 'admin':
        return p
    if user['rol'] == 'chofer' and p['chofer_id'] is not None and str(p['chofer_id']) == str(user['id']):
        return p
    if user['rol'] == 'cliente' and (str(p['solicitante_id']) == str(user['id']) or (p['cliente_id'] is not None and p['cliente_id'] == user.get('cliente_id'))):
        return p
    raise HTTPException(404, "Parada no encontrada.")


@router.post("/paradas/{documento_id}/entrega")
async def registrar_entrega(documento_id: int, data: EntregaData, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if user['rol'] not in ('chofer', 'admin'):
        raise HTTPException(403, "Acceso denegado.")
    p = await _parada_del_usuario(db, documento_id, user)
    if p['flete_estado'] != 'camino':
        raise HTTPException(400, "Solo se registran entregas de fletes en camino.")
    if data.estado not in ('entregada', 'no_entregada'):
        raise HTTPException(400, "Estado invalido.")
    if data.estado == 'entregada' and not data.recibido_por.strip():
        raise HTTPException(400, "Indique quien recibio.")
    if data.estado == 'no_entregada' and data.motivo not in MOTIVOS:
        raise HTTPException(400, "Indique el motivo de la incidencia.")
    firma, foto = _imagen(data.firma, "firma"), _imagen(data.foto, "foto")
    async with db.transaction():
        await db.execute("""
            INSERT INTO entregas (documento_id, estado, recibido_por, motivo, observaciones, firma, foto, lat, lon, chofer_id, registrado_en)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::uuid, NOW())
            ON CONFLICT (documento_id) DO UPDATE SET estado = EXCLUDED.estado, recibido_por = EXCLUDED.recibido_por,
                motivo = EXCLUDED.motivo, observaciones = EXCLUDED.observaciones, firma = EXCLUDED.firma, foto = EXCLUDED.foto,
                lat = EXCLUDED.lat, lon = EXCLUDED.lon, chofer_id = EXCLUDED.chofer_id, registrado_en = NOW()
        """, documento_id, data.estado, data.recibido_por.strip(), data.motivo if data.estado == 'no_entregada' else '',
            data.observaciones.strip(), firma, foto, data.lat, data.lon, user['id'])
        ubicada = False
        # Entregada en una parada sin ubicacion: el GPS del chofer la ubica (y a la direccion guardada, si la hay).
        if data.estado == 'entregada' and data.lat is not None and data.lon is not None and p['destino_lat'] is None:
            await db.execute("UPDATE documentos_flete SET destino_lat = $1, destino_lon = $2 WHERE id = $3", data.lat, data.lon, documento_id)
            if p['destino_id']:
                tabla = "sucursales" if p['destino_tipo'] == "sucursal" else "direcciones_cliente"
                await db.execute(f"UPDATE {tabla} SET latitud = $1, longitud = $2 WHERE id = $3 AND latitud IS NULL", data.lat, data.lon, p['destino_id'])
            ubicada = True
    return {"status": "success", "ubicacion_guardada": ubicada}


@router.get("/paradas/{documento_id}/{imagen}")
async def ver_imagen(documento_id: int, imagen: str, db: asyncpg.Connection = Depends(get_db), user=Depends(get_current_user)):
    if imagen not in ("firma", "foto"):
        raise HTTPException(404, "No encontrado.")
    await _parada_del_usuario(db, documento_id, user)
    crudo = await db.fetchval(f"SELECT {imagen} FROM entregas WHERE documento_id = $1", documento_id)
    if not crudo:
        raise HTTPException(404, "Sin imagen.")
    return Response(content=bytes(crudo), media_type=_tipo(bytes(crudo)), headers={"Cache-Control": "private, no-store"})


# --- SEGUIMIENTO PUBLICO ---
@router.get("/seguimiento/{token}")
async def seguimiento(token: str, db: asyncpg.Connection = Depends(get_db)):
    """Publico (sin sesion): lo abre quien tenga el link. Datos minimos: estado, paradas (alias y ciudad), hora de
    cada entrega y, en camino, una posicion aproximada del vehiculo (redondeada a ~1 km). Sin nombres de quien
    recibio ni datos del cliente."""
    if len(token) < 16:
        raise HTTPException(404, "Envio no encontrado.")
    f = await db.fetchrow("""
        SELECT f.id, f.estado, f.creado_en, split_part(COALESCE(ch.nombre_completo, ''), ' ', 1) AS chofer,
               (SELECT nombre_empresa FROM configuracion_sistema WHERE id = 1) AS empresa,
               ch.lat_actual, ch.lon_actual, ch.ultima_conexion
        FROM fletes f LEFT JOIN usuarios ch ON ch.id = f.chofer_id WHERE f.token_seguimiento = $1""", token)
    if not f:
        raise HTTPException(404, "Envio no encontrado.")
    paradas = await db.fetch("""
        SELECT d.orden_ruta, d.destino_alias, d.destino_ciudad, d.ventana_desde, d.ventana_hasta, e.estado, e.registrado_en
        FROM documentos_flete d LEFT JOIN entregas e ON e.documento_id = d.id WHERE d.flete_id = $1 ORDER BY d.orden_ruta""", f['id'])
    posicion = None
    if f['estado'] == 'camino' and f['lat_actual'] is not None:
        posicion = {"lat": round(f['lat_actual'], 2), "lon": round(f['lon_actual'], 2),
                    "actualizada": f['ultima_conexion'].isoformat() if f['ultima_conexion'] else None}
    hhmm = lambda t: t.strftime("%H:%M") if t else None
    return {
        "numero": f['id'], "estado": f['estado'], "empresa": f['empresa'], "creado_en": f['creado_en'].isoformat(),
        "chofer": f['chofer'] or None, "posicion": posicion,
        "paradas": [{"orden": p['orden_ruta'], "destino": p['destino_alias'], "ciudad": p['destino_ciudad'],
                     "ventana": [hhmm(p['ventana_desde']), hhmm(p['ventana_hasta'])], "estado": p['estado'] or 'pendiente',
                     "registrado_en": p['registrado_en'].isoformat() if p['registrado_en'] else None} for p in paradas],
    }
