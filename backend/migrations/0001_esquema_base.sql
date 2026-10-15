-- Esquema base de JZTravell (antes init_db.sql, que cargaba Postgres una sola vez). Lo aplica la app al
-- arrancar (jztech_core.migrations, tabla schema_version). Todo es idempotente (IF NOT EXISTS / ON CONFLICT),
-- asi que sobre una base creada por init_db.sql no cambia los datos. Los cambios de esquema nuevos van en
-- archivos 0002_..., 0003_... (nunca editar uno ya publicado).
--
-- El primer administrador no se crea aca: con la tabla usuarios vacia, la app muestra la configuracion
-- inicial (POST /api/setup/admin), que pide el SETUP_TOKEN del instalador.
-- gen_random_uuid() viene con PostgreSQL 13+ (no hace falta pgcrypto).

-- === USUARIOS Y SESIONES ===
CREATE TABLE IF NOT EXISTS usuarios (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    nombre_completo TEXT NOT NULL DEFAULT '',
    rol TEXT NOT NULL CHECK (rol IN ('admin', 'cliente', 'chofer')),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    intentos_fallidos INT NOT NULL DEFAULT 0,
    bloqueado_hasta TIMESTAMP,
    lat_actual DOUBLE PRECISION,
    lon_actual DOUBLE PRECISION,
    ultima_conexion TIMESTAMP
);

-- Sesiones de jztech_core.sessions: solo se guarda el hash SHA-256 del token (el valor en claro vive
-- en la cookie). Reemplaza a sesiones_activas (versiones hasta 1.0.x): al actualizar se cierran las
-- sesiones abiertas y cada usuario vuelve a iniciar sesion una vez.
CREATE TABLE IF NOT EXISTS jztech_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS jztech_sessions_user_id_idx ON jztech_sessions (user_id);
DROP TABLE IF EXISTS sesiones_activas;

-- Rate limit de login por IP.
CREATE TABLE IF NOT EXISTS login_rate_limit (
    ip TEXT PRIMARY KEY,
    intentos INT NOT NULL DEFAULT 0,
    bloqueado_hasta TIMESTAMP
);

-- === CONFIGURACIÓN GLOBAL (fila única, id=1) ===
CREATE TABLE IF NOT EXISTS configuracion_sistema (
    id INT PRIMARY KEY DEFAULT 1,
    nombre_empresa TEXT NOT NULL DEFAULT 'Mi Empresa',
    gps_polling_sec INT NOT NULL DEFAULT 30,
    google_oauth_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    google_client_id TEXT,
    google_client_secret TEXT,
    google_redirect_url TEXT,
    ciudad_defecto TEXT NOT NULL DEFAULT '',
    provincia_defecto TEXT NOT NULL DEFAULT '',
    pais_defecto TEXT NOT NULL DEFAULT 'Argentina',
    manejar_volumenes BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT solo_una_fila CHECK (id = 1)
);
INSERT INTO configuracion_sistema (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- === ALTAS PENDIENTES (autoregistro vía Google) ===
CREATE TABLE IF NOT EXISTS solicitudes_registro (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    nombre_completo TEXT NOT NULL DEFAULT '',
    creado_en TIMESTAMP NOT NULL DEFAULT NOW()
);

-- === SUCURSALES (puntos de origen) ===
CREATE TABLE IF NOT EXISTS sucursales (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    calle TEXT NOT NULL DEFAULT '',
    altura TEXT NOT NULL DEFAULT '',
    ciudad TEXT NOT NULL DEFAULT '',
    codigo_postal TEXT NOT NULL DEFAULT '',
    provincia TEXT NOT NULL DEFAULT '',
    latitud DOUBLE PRECISION NOT NULL DEFAULT -34.6037,
    longitud DOUBLE PRECISION NOT NULL DEFAULT -58.3816,
    activa BOOLEAN NOT NULL DEFAULT TRUE
);

-- === FLOTA DE VEHÍCULOS ===
CREATE TABLE IF NOT EXISTS flota_vehiculos (
    id SERIAL PRIMARY KEY,
    patente_identificador TEXT NOT NULL,
    marca_modelo TEXT NOT NULL DEFAULT '',
    tipo TEXT NOT NULL DEFAULT '',
    autonomia_maxima_km DOUBLE PRECISION NOT NULL DEFAULT 0,
    capacidad_volumen_m3 DOUBLE PRECISION NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'mantenimiento', 'baja'))
);

-- === CLIENTES Y SUS DIRECCIONES ===
CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY,
    razon_social TEXT NOT NULL,
    cuit_rut TEXT UNIQUE NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS direcciones_cliente (
    id SERIAL PRIMARY KEY,
    cliente_id INT NOT NULL REFERENCES clientes(id),
    alias TEXT NOT NULL DEFAULT '',
    calle TEXT NOT NULL DEFAULT '',
    altura TEXT NOT NULL DEFAULT '',
    ciudad TEXT NOT NULL DEFAULT '',
    codigo_postal TEXT NOT NULL DEFAULT '',
    provincia TEXT NOT NULL DEFAULT '',
    latitud DOUBLE PRECISION NOT NULL DEFAULT -34.6037,
    longitud DOUBLE PRECISION NOT NULL DEFAULT -58.3816
);

-- === CATÁLOGO DE TIPOS DE DOCUMENTO ===
-- La app solo LEE esta tabla (GET /api/tipos_documentos); no hay endpoint para administrarla
-- desde la UI, así que tiene que venir precargada. Columnas confirmadas en frontend/admin.html
-- (DB.tipos_docs.map(t => t.codigo, t.nombre), y los flags requiere_id/requiere_pto_venta/requiere_numero).
CREATE TABLE IF NOT EXISTS tipos_documentos (
    id SERIAL PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    requiere_id BOOLEAN NOT NULL DEFAULT FALSE,
    requiere_pto_venta BOOLEAN NOT NULL DEFAULT FALSE,
    requiere_numero BOOLEAN NOT NULL DEFAULT FALSE,
    activo BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO tipos_documentos (codigo, nombre, requiere_id, requiere_pto_venta, requiere_numero) VALUES
    ('remito', 'Remito', TRUE, TRUE, TRUE),
    ('odt', 'ODT', TRUE, FALSE, FALSE)
ON CONFLICT (codigo) DO NOTHING;

-- === FLETES Y SUS PARADAS ===
CREATE TABLE IF NOT EXISTS fletes (
    id SERIAL PRIMARY KEY,
    solicitante_id UUID NOT NULL REFERENCES usuarios(id),
    vehiculo_id INT NOT NULL REFERENCES flota_vehiculos(id),
    origen_id INT NOT NULL REFERENCES sucursales(id),
    prioridad TEXT NOT NULL DEFAULT 'normal',
    distancia_total_km DOUBLE PRECISION NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    creado_en TIMESTAMP NOT NULL DEFAULT NOW(),
    chofer_id UUID REFERENCES usuarios(id)
);
CREATE INDEX IF NOT EXISTS idx_fletes_estado ON fletes(estado);

CREATE TABLE IF NOT EXISTS documentos_flete (
    id SERIAL PRIMARY KEY,
    flete_id INT NOT NULL REFERENCES fletes(id) ON DELETE CASCADE,
    tipo_documento TEXT NOT NULL,
    doc_id TEXT NOT NULL DEFAULT '',
    punto_venta TEXT NOT NULL DEFAULT '',
    numero TEXT NOT NULL DEFAULT '',
    destino_tipo TEXT NOT NULL CHECK (destino_tipo IN ('sucursal', 'cliente')),
    destino_id INT NOT NULL DEFAULT 0,
    destino_alias TEXT NOT NULL DEFAULT '',
    destino_calle TEXT NOT NULL DEFAULT '',
    destino_altura TEXT NOT NULL DEFAULT '',
    destino_ciudad TEXT NOT NULL DEFAULT '',
    destino_codigo_postal TEXT NOT NULL DEFAULT '',
    destino_provincia TEXT NOT NULL DEFAULT '',
    orden_ruta INT NOT NULL DEFAULT 0,
    bultos INT NOT NULL DEFAULT 1,
    volumen_m3 DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documentos_flete_flete_id ON documentos_flete(flete_id);
