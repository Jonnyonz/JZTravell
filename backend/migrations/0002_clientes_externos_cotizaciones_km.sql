-- 1.2.0: clientes externos, cotizaciones, volumen por vehiculo y km recorridos (sin servicios externos).

-- === VEHICULOS: volumen propio y costos para cotizar ===
-- usa_volumen reemplaza al ajuste global configuracion_sistema.manejar_volumenes (se copia a todos los
-- vehiculos existentes; la columna global queda sin uso).
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS usa_volumen BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE flota_vehiculos SET usa_volumen = (SELECT manejar_volumenes FROM configuracion_sistema WHERE id = 1);
-- consumo: litros (combustion) o kWh (electrico) cada 100 km.
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS consumo_100km DOUBLE PRECISION NOT NULL DEFAULT 0;
-- costo por km de desgaste: cubiertas, service, repuestos, amortizacion.
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS costo_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS odometro_km DOUBLE PRECISION NOT NULL DEFAULT 0;

-- === PARAMETROS GENERALES DE COSTOS ===
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS precio_litro DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS precio_kwh DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS costo_chofer_hora DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS velocidad_promedio_kmh DOUBLE PRECISION NOT NULL DEFAULT 40;
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS minutos_por_parada DOUBLE PRECISION NOT NULL DEFAULT 15;
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS margen_pct DOUBLE PRECISION NOT NULL DEFAULT 20;
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS iva_pct DOUBLE PRECISION NOT NULL DEFAULT 21;
-- km por calle = km en linea recta x factor_calle (se puede recalibrar con los viajes hechos).
ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS factor_calle DOUBLE PRECISION NOT NULL DEFAULT 1.3;

-- === USUARIOS CLIENTE: empresa a la que pertenecen ===
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cliente_id INT REFERENCES clientes(id);

-- === RUTAS: lo que el chofer consolida y recorre de una vez ===
CREATE TABLE IF NOT EXISTS rutas (
    id SERIAL PRIMARY KEY,
    chofer_id UUID NOT NULL REFERENCES usuarios(id),
    vehiculo_id INT REFERENCES flota_vehiculos(id),
    iniciada_en TIMESTAMP NOT NULL DEFAULT NOW(),
    finalizada_en TIMESTAMP,
    odometro_salida DOUBLE PRECISION,
    odometro_llegada DOUBLE PRECISION,
    km_linea_recta DOUBLE PRECISION NOT NULL DEFAULT 0,
    km_estimados DOUBLE PRECISION NOT NULL DEFAULT 0,
    km_gps DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_rutas_chofer ON rutas(chofer_id);

-- Posiciones GPS del chofer mientras tiene una ruta en curso (para medir los km recorridos).
CREATE TABLE IF NOT EXISTS ruta_gps (
    id BIGSERIAL PRIMARY KEY,
    ruta_id INT NOT NULL REFERENCES rutas(id) ON DELETE CASCADE,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    precision_m DOUBLE PRECISION,
    registrado_en TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ruta_gps_ruta ON ruta_gps(ruta_id, registrado_en);

-- === FLETES: externos (servicio a terceros), origen en cualquier direccion, precio ===
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'interno';
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fletes_tipo_check') THEN
        ALTER TABLE fletes ADD CONSTRAINT fletes_tipo_check CHECK (tipo IN ('interno', 'externo'));
    END IF;
END $$;
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS cliente_id INT REFERENCES clientes(id);
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS precio DOUBLE PRECISION;
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS ruta_id INT REFERENCES rutas(id);
-- Origen fuera de las sucursales propias (retiro en la direccion del cliente): origen_id queda NULL.
ALTER TABLE fletes ALTER COLUMN origen_id DROP NOT NULL;
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_alias TEXT NOT NULL DEFAULT '';
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_calle TEXT NOT NULL DEFAULT '';
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_altura TEXT NOT NULL DEFAULT '';
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_ciudad TEXT NOT NULL DEFAULT '';
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_provincia TEXT NOT NULL DEFAULT '';
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_lat DOUBLE PRECISION;
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS origen_lon DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_fletes_cliente ON fletes(cliente_id);

-- Coordenadas de cada parada (antes se buscaban al consolidar; las esporadicas no tenian).
ALTER TABLE documentos_flete ADD COLUMN IF NOT EXISTS destino_lat DOUBLE PRECISION;
ALTER TABLE documentos_flete ADD COLUMN IF NOT EXISTS destino_lon DOUBLE PRECISION;

-- Direcciones y sucursales sin coordenadas reales: hasta ahora todas quedaban en el centro de CABA
-- (-34.6037, -58.3816). Se marcan como "sin ubicar" (NULL) para no calcular distancias con ellas.
ALTER TABLE sucursales ALTER COLUMN latitud DROP NOT NULL;
ALTER TABLE sucursales ALTER COLUMN longitud DROP NOT NULL;
ALTER TABLE sucursales ALTER COLUMN latitud DROP DEFAULT;
ALTER TABLE sucursales ALTER COLUMN longitud DROP DEFAULT;
UPDATE sucursales SET latitud = NULL, longitud = NULL WHERE latitud = -34.6037 AND longitud = -58.3816;
ALTER TABLE direcciones_cliente ALTER COLUMN latitud DROP NOT NULL;
ALTER TABLE direcciones_cliente ALTER COLUMN longitud DROP NOT NULL;
ALTER TABLE direcciones_cliente ALTER COLUMN latitud DROP DEFAULT;
ALTER TABLE direcciones_cliente ALTER COLUMN longitud DROP DEFAULT;
UPDATE direcciones_cliente SET latitud = NULL, longitud = NULL WHERE latitud = -34.6037 AND longitud = -58.3816;

-- === COTIZACIONES ===
-- Las pide un cliente desde su portal (estado 'solicitada') o las arma el admin. El admin les pone
-- vehiculo y km y la app calcula el desglose; 'enviada' = el cliente la ve con precio; al aceptarla se
-- crea el flete externo (flete_id).
CREATE TABLE IF NOT EXISTS cotizaciones (
    id SERIAL PRIMARY KEY,
    cliente_id INT REFERENCES clientes(id),
    solicitante_id UUID REFERENCES usuarios(id),
    estado TEXT NOT NULL DEFAULT 'solicitada'
        CHECK (estado IN ('solicitada', 'enviada', 'aceptada', 'rechazada')),
    descripcion TEXT NOT NULL DEFAULT '',
    fecha_viaje DATE,
    -- origen y paradas: {alias, calle, altura, ciudad, provincia, lat, lon} (+ bultos, volumen_m3 en paradas)
    origen JSONB NOT NULL DEFAULT '{}'::jsonb,
    paradas JSONB NOT NULL DEFAULT '[]'::jsonb,
    vehiculo_id INT REFERENCES flota_vehiculos(id),
    km_estimados DOUBLE PRECISION NOT NULL DEFAULT 0,
    horas_estimadas DOUBLE PRECISION NOT NULL DEFAULT 0,
    peajes DOUBLE PRECISION NOT NULL DEFAULT 0,
    otros_gastos DOUBLE PRECISION NOT NULL DEFAULT 0,
    costo_energia DOUBLE PRECISION NOT NULL DEFAULT 0,
    costo_desgaste DOUBLE PRECISION NOT NULL DEFAULT 0,
    costo_chofer DOUBLE PRECISION NOT NULL DEFAULT 0,
    subtotal DOUBLE PRECISION NOT NULL DEFAULT 0,
    margen_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    margen DOUBLE PRECISION NOT NULL DEFAULT 0,
    iva_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    iva DOUBLE PRECISION NOT NULL DEFAULT 0,
    total DOUBLE PRECISION NOT NULL DEFAULT 0,
    notas TEXT NOT NULL DEFAULT '',
    flete_id INT REFERENCES fletes(id),
    creado_en TIMESTAMP NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cotizaciones_cliente ON cotizaciones(cliente_id);
