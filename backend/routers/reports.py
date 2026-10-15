"""Reportes del admin para un periodo: fletes, entregas a tiempo, incidencias, facturacion por cliente, km y
mantenimiento por vehiculo, y costo real contra ingresos de cada ruta."""
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
import asyncpg

import calculos
from database import get_db, require_role

router = APIRouter(prefix="/api", tags=["Reportes"])

COSTOS = "precio_litro, precio_kwh, costo_chofer_hora"


@router.get("/reportes")
async def reportes(desde: Optional[date] = None, hasta: Optional[date] = None, db: asyncpg.Connection = Depends(get_db),
                   user=Depends(require_role(['admin']))):
    hasta = hasta or date.today()
    desde = desde or hasta - timedelta(days=30)
    if desde > hasta:
        raise HTTPException(400, "El periodo empieza despues de terminar.")
    rango = (desde, hasta + timedelta(days=1))  # [desde, hasta] inclusive, sobre columnas TIMESTAMP

    fletes = await db.fetchrow("""
        SELECT COUNT(*) AS creados,
               COUNT(*) FILTER (WHERE estado = 'completado') AS completados,
               COUNT(*) FILTER (WHERE estado = 'problema') AS con_incidencia,
               COUNT(*) FILTER (WHERE tipo = 'externo') AS externos,
               COALESCE(SUM(precio) FILTER (WHERE tipo = 'externo' AND estado = 'completado'), 0) AS facturado
        FROM fletes WHERE creado_en >= $1 AND creado_en < $2""", *rango)

    entregas = await db.fetch("""
        SELECT e.estado, e.motivo, e.registrado_en, d.ventana_desde, d.ventana_hasta
        FROM entregas e JOIN documentos_flete d ON d.id = e.documento_id
        WHERE e.registrado_en >= $1 AND e.registrado_en < $2""", *rango)
    con_ventana = [calculos.a_tiempo(e['registrado_en'].time(), e['ventana_desde'], e['ventana_hasta'])
                   for e in entregas if e['estado'] == 'entregada']
    con_ventana = [x for x in con_ventana if x is not None]
    motivos = {}
    for e in entregas:
        if e['estado'] == 'no_entregada':
            motivos[e['motivo']] = motivos.get(e['motivo'], 0) + 1

    por_cliente = [dict(r) for r in await db.fetch("""
        SELECT c.razon_social AS cliente, COUNT(*) AS fletes,
               COUNT(*) FILTER (WHERE f.estado = 'completado') AS completados,
               COALESCE(SUM(f.precio) FILTER (WHERE f.estado = 'completado'), 0) AS facturado
        FROM fletes f JOIN clientes c ON c.id = f.cliente_id
        WHERE f.tipo = 'externo' AND f.creado_en >= $1 AND f.creado_en < $2
        GROUP BY c.razon_social ORDER BY facturado DESC""", *rango)]

    cfg = dict(await db.fetchrow(f"SELECT {COSTOS} FROM configuracion_sistema WHERE id = 1"))
    rutas = []
    for r in await db.fetch("""
        SELECT r.*, u.nombre_completo AS chofer, v.patente_identificador, v.tipo, v.consumo_100km, v.costo_km,
               (SELECT COALESCE(SUM(f.precio), 0) FROM fletes f WHERE f.ruta_id = r.id AND f.tipo = 'externo') AS ingresos,
               (SELECT COUNT(*) FROM fletes f WHERE f.ruta_id = r.id) AS fletes
        FROM rutas r JOIN usuarios u ON u.id = r.chofer_id LEFT JOIN flota_vehiculos v ON v.id = r.vehiculo_id
        WHERE r.finalizada_en IS NOT NULL AND r.iniciada_en >= $1 AND r.iniciada_en < $2 ORDER BY r.id""", *rango):
        km_reales = (r['odometro_llegada'] - r['odometro_salida']) if r['odometro_llegada'] is not None and r['odometro_salida'] is not None else r['km_gps']
        horas = (r['finalizada_en'] - r['iniciada_en']).total_seconds() / 3600
        costo = calculos.costo_real_ruta(km_reales if km_reales is not None else r['km_estimados'], horas,
                                         {"tipo": r['tipo'], "consumo_100km": r['consumo_100km'], "costo_km": r['costo_km']}, cfg,
                                         r['gasto_combustible'], r['gasto_peajes'], r['gasto_otros'])
        rutas.append({"id": r['id'], "fecha": r['iniciada_en'].date().isoformat(), "chofer": r['chofer'],
                      "vehiculo": r['patente_identificador'], "fletes": r['fletes'], "km_estimados": r['km_estimados'],
                      "km_reales": round(km_reales, 2) if km_reales is not None else None, "horas": round(horas, 2),
                      "costo": costo, "ingresos": r['ingresos'], "resultado": round(r['ingresos'] - costo['total'], 2)})

    por_vehiculo = {}
    for r in rutas:
        v = por_vehiculo.setdefault(r['vehiculo'] or '-', {"vehiculo": r['vehiculo'] or '-', "rutas": 0, "km_reales": 0.0, "km_estimados": 0.0, "mantenimiento": 0.0})
        v["rutas"] += 1
        v["km_reales"] += r['km_reales'] or 0
        v["km_estimados"] += r['km_estimados'] or 0
    for m in await db.fetch("""
        SELECT v.patente_identificador, SUM(m.costo) AS costo FROM mantenimientos m JOIN flota_vehiculos v ON v.id = m.vehiculo_id
        WHERE m.fecha >= $1 AND m.fecha <= $2 GROUP BY v.patente_identificador""", desde, hasta):
        v = por_vehiculo.setdefault(m['patente_identificador'], {"vehiculo": m['patente_identificador'], "rutas": 0, "km_reales": 0.0, "km_estimados": 0.0, "mantenimiento": 0.0})
        v["mantenimiento"] = m['costo']
    for v in por_vehiculo.values():
        v["km_reales"], v["km_estimados"] = round(v["km_reales"], 2), round(v["km_estimados"], 2)

    entregadas = sum(1 for e in entregas if e['estado'] == 'entregada')
    return {
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "fletes": dict(fletes),
        "entregas": {"registradas": len(entregas), "entregadas": entregadas, "no_entregadas": len(entregas) - entregadas,
                     "con_ventana": len(con_ventana), "a_tiempo": sum(con_ventana),
                     "pct_a_tiempo": round(100 * sum(con_ventana) / len(con_ventana), 1) if con_ventana else None,
                     "motivos": motivos},
        "por_cliente": por_cliente,
        "por_vehiculo": sorted(por_vehiculo.values(), key=lambda v: v["vehiculo"]),
        "rutas": rutas,
        "totales_rutas": {"km_reales": round(sum(r['km_reales'] or 0 for r in rutas), 2),
                          "costo": round(sum(r['costo']['total'] for r in rutas), 2),
                          "ingresos": round(sum(r['ingresos'] for r in rutas), 2),
                          "resultado": round(sum(r['resultado'] for r in rutas), 2)},
    }
