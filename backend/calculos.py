"""Calculos sin base de datos: distancias, km recorridos por GPS y costo de una cotizacion.

Sin servicios externos de ruteo (OSRM quedo descartado por pesado): la distancia por calle se estima
como linea recta x factor_calle, y ese factor se puede recalibrar con los km reales de los viajes hechos.
"""
import math
from datetime import datetime

RADIO_TIERRA_KM = 6371.0
# Rastro GPS: se descartan puntos imprecisos y saltos imposibles (un pulso con el GPS desubicado).
GPS_PRECISION_MAX_M = 60.0
GPS_VELOCIDAD_MAX_KMH = 150.0
GPS_MOVIMIENTO_MIN_KM = 0.015  # 15 m: por debajo es ruido de un vehiculo parado


def haversine_km(lat1, lon1, lat2, lon2):
    """Distancia en linea recta (km) entre dos coordenadas; 0 si falta alguna."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    rad = math.pi / 180
    dlat = (lat2 - lat1) * rad
    dlon = (lon2 - lon1) * rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlon / 2) ** 2
    return RADIO_TIERRA_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ubicado(p):
    return p.get("lat") is not None and p.get("lon") is not None


def ordenar_paradas(origen, paradas):
    """Vecino mas cercano desde el origen. Las paradas sin coordenadas van al final, en su orden."""
    con = [p for p in paradas if ubicado(p)]
    sin = [p for p in paradas if not ubicado(p)]
    if not ubicado(origen):
        return con + sin
    orden, actual = [], origen
    while con:
        proxima = min(con, key=lambda p: haversine_km(actual["lat"], actual["lon"], p["lat"], p["lon"]))
        con.remove(proxima)
        orden.append(proxima)
        actual = proxima
    return orden + sin


def km_linea_recta(origen, paradas):
    """Suma de tramos en linea recta origen -> parada 1 -> ... (solo entre puntos ubicados)."""
    puntos = [p for p in [origen, *paradas] if ubicado(p)]
    return sum(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(puntos, puntos[1:]))


def faltan_coordenadas(origen, paradas):
    return [p.get("alias") or p.get("calle") or "?" for p in [origen, *paradas] if not ubicado(p)]


def km_gps(puntos):
    """Km recorridos segun el rastro GPS: puntos (lat, lon, precision_m, registrado_en) en orden.
    Descarta puntos con mala precision, saltos a mas de GPS_VELOCIDAD_MAX_KMH y movimientos minimos."""
    total, anterior = 0.0, None
    for lat, lon, precision, cuando in puntos:
        if precision is not None and precision > GPS_PRECISION_MAX_M:
            continue
        if anterior is None:
            anterior = (lat, lon, cuando)
            continue
        d = haversine_km(anterior[0], anterior[1], lat, lon)
        horas = max((cuando - anterior[2]).total_seconds(), 1) / 3600
        if d / horas > GPS_VELOCIDAD_MAX_KMH:
            continue  # salto imposible: se ignora este punto y se sigue desde el anterior bueno
        if d < GPS_MOVIMIENTO_MIN_KM:
            continue
        total += d
        anterior = (lat, lon, cuando)
    return round(total, 2)


def factor_sugerido(viajes):
    """Factor calle = km reales / km en linea recta, promedio de viajes [(km_reales, km_linea)].
    Ignora viajes cortos (< 2 km en linea recta) y relaciones absurdas. None si no hay datos."""
    ratios = [r / l for r, l in viajes if l and l >= 2 and r and 1.0 <= r / l <= 3.0]
    if not ratios:
        return None
    return round(sum(ratios) / len(ratios), 2)


def costo_cotizacion(km, paradas, vehiculo, cfg, peajes=0.0, otros=0.0, margen_pct=None, iva_pct=None):
    """Desglose de una cotizacion. vehiculo: tipo, consumo_100km, costo_km. cfg: precio_litro, precio_kwh,
    costo_chofer_hora, velocidad_promedio_kmh, minutos_por_parada, margen_pct, iva_pct."""
    km = max(float(km or 0), 0.0)
    precio_energia = cfg["precio_kwh"] if vehiculo.get("tipo") == "electrico" else cfg["precio_litro"]
    velocidad = cfg["velocidad_promedio_kmh"] or 40
    horas = km / velocidad + paradas * (cfg["minutos_por_parada"] or 0) / 60
    margen_pct = cfg["margen_pct"] if margen_pct is None else margen_pct
    iva_pct = cfg["iva_pct"] if iva_pct is None else iva_pct

    costo_energia = km * (vehiculo.get("consumo_100km") or 0) / 100 * (precio_energia or 0)
    costo_desgaste = km * (vehiculo.get("costo_km") or 0)
    costo_chofer = horas * (cfg["costo_chofer_hora"] or 0)
    subtotal = costo_energia + costo_desgaste + costo_chofer + (peajes or 0) + (otros or 0)
    margen = subtotal * (margen_pct or 0) / 100
    iva = (subtotal + margen) * (iva_pct or 0) / 100
    r2 = lambda v: round(v, 2)
    return {
        "km_estimados": r2(km), "horas_estimadas": r2(horas), "peajes": r2(peajes or 0), "otros_gastos": r2(otros or 0),
        "costo_energia": r2(costo_energia), "costo_desgaste": r2(costo_desgaste), "costo_chofer": r2(costo_chofer),
        "subtotal": r2(subtotal), "margen_pct": margen_pct, "margen": r2(margen), "iva_pct": iva_pct,
        "iva": r2(iva), "total": r2(subtotal + margen + iva),
    }


if __name__ == "__main__":
    # Autochequeo rapido: python calculos.py
    obelisco, retiro = {"lat": -34.6037, "lon": -58.3816}, {"lat": -34.5915, "lon": -58.3747}
    assert 1.4 < haversine_km(obelisco["lat"], obelisco["lon"], retiro["lat"], retiro["lon"]) < 1.5
    t = datetime(2026, 1, 1, 10, 0, 0)
    from datetime import timedelta as td
    pts = [(-34.6037, -58.3816, 10, t), (-34.5915, -58.3747, 10, t + td(minutes=5)),
           (-30.0, -60.0, 10, t + td(minutes=6)),            # salto imposible
           (-34.5915, -58.3747, 500, t + td(minutes=7)),      # impreciso
           (-34.6037, -58.3816, 10, t + td(minutes=12))]
    assert 2.8 < km_gps(pts) < 3.0, km_gps(pts)
    assert factor_sugerido([(13, 10), (14, 10), (100, 1)]) == 1.35
    c = costo_cotizacion(100, 2, {"tipo": "combustion", "consumo_100km": 12, "costo_km": 50},
                         {"precio_litro": 1000, "precio_kwh": 0, "costo_chofer_hora": 6000, "velocidad_promedio_kmh": 50,
                          "minutos_por_parada": 15, "margen_pct": 20, "iva_pct": 21})
    assert c["costo_energia"] == 12000 and c["costo_desgaste"] == 5000 and c["horas_estimadas"] == 2.5
    assert c["costo_chofer"] == 15000 and c["subtotal"] == 32000 and c["total"] == round(32000 * 1.2 * 1.21, 2)
    o = ordenar_paradas(obelisco, [{"alias": "lejos", "lat": -34.9, "lon": -57.9}, {"alias": "sin"},
                                   {"alias": "cerca", "lat": -34.59, "lon": -58.37}])
    assert [p["alias"] for p in o] == ["cerca", "lejos", "sin"]
    print("ok")
