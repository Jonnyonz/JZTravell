-- 1.3.0: prueba de entrega e incidencias, link de seguimiento, vencimientos, mantenimiento, costo real de las rutas
-- y ventanas horarias.

-- === PRUEBA DE ENTREGA / INCIDENCIA, POR PARADA ===
-- 'entregada': quien recibio, firma y foto opcionales. 'no_entregada': motivo (incidencia) y foto opcional.
-- Firma y foto se guardan en la base (PNG/JPEG chicos, comprimidos en el celular): entran en el respaldo.
CREATE TABLE IF NOT EXISTS entregas (
    documento_id INT PRIMARY KEY REFERENCES documentos_flete(id) ON DELETE CASCADE,
    estado TEXT NOT NULL CHECK (estado IN ('entregada', 'no_entregada')),
    recibido_por TEXT NOT NULL DEFAULT '',
    motivo TEXT NOT NULL DEFAULT '',
    observaciones TEXT NOT NULL DEFAULT '',
    firma BYTEA,
    foto BYTEA,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    chofer_id UUID REFERENCES usuarios(id),
    registrado_en TIMESTAMP NOT NULL DEFAULT NOW()
);

-- === LINK DE SEGUIMIENTO (publico, sin cuenta) ===
ALTER TABLE fletes ADD COLUMN IF NOT EXISTS token_seguimiento TEXT;
UPDATE fletes SET token_seguimiento = replace(gen_random_uuid()::text, '-', '') WHERE token_seguimiento IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_fletes_token ON fletes(token_seguimiento);

-- === VENTANAS HORARIAS DE ENTREGA ===
ALTER TABLE documentos_flete ADD COLUMN IF NOT EXISTS ventana_desde TIME;
ALTER TABLE documentos_flete ADD COLUMN IF NOT EXISTS ventana_hasta TIME;

-- === VENCIMIENTOS ===
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS vtv_vence DATE;
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS seguro_vence DATE;
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS habilitacion_vence DATE;  -- RUTA / habilitacion de transporte
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS licencia_vence DATE;

-- === MANTENIMIENTO ===
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS service_cada_km DOUBLE PRECISION NOT NULL DEFAULT 0;  -- 0 = sin aviso
ALTER TABLE flota_vehiculos ADD COLUMN IF NOT EXISTS ultimo_service_km DOUBLE PRECISION NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS mantenimientos (
    id SERIAL PRIMARY KEY,
    vehiculo_id INT NOT NULL REFERENCES flota_vehiculos(id) ON DELETE CASCADE,
    fecha DATE NOT NULL DEFAULT CURRENT_DATE,
    odometro_km DOUBLE PRECISION,
    tipo TEXT NOT NULL DEFAULT 'service' CHECK (tipo IN ('service', 'reparacion', 'cubiertas', 'otro')),
    descripcion TEXT NOT NULL DEFAULT '',
    costo DOUBLE PRECISION NOT NULL DEFAULT 0,
    creado_en TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mantenimientos_vehiculo ON mantenimientos(vehiculo_id, fecha);

-- === COSTO REAL DE CADA RUTA (lo carga el chofer al cerrarla) ===
ALTER TABLE rutas ADD COLUMN IF NOT EXISTS gasto_combustible DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE rutas ADD COLUMN IF NOT EXISTS gasto_peajes DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE rutas ADD COLUMN IF NOT EXISTS gasto_otros DOUBLE PRECISION NOT NULL DEFAULT 0;
