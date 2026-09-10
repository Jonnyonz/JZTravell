-- Esquema de base de datos de JZ Travel.
-- Reconstruido a partir de las queries SQL usadas en backend/main.py y del contrato de
-- datos esperado por el frontend (frontend/admin.html, cliente.html), ya que este archivo
-- estaba vacío en el repositorio y Docker Compose lo usa para inicializar Postgres desde cero.
-- Se ejecuta automáticamente una sola vez, la primera vez que se levanta el contenedor de la base
-- (docker-entrypoint-initdb.d), solo si el volumen de datos está vacío.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- === USUARIOS Y SESIONES ===
CREATE TABLE usuarios (
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

CREATE TABLE sesiones_activas (
    id SERIAL PRIMARY KEY,
    usuario_id UUID NOT NULL REFERENCES usuarios(id),
    token_sesion TEXT UNIQUE NOT NULL,
    expira_en TIMESTAMP NOT NULL
);
CREATE INDEX idx_sesiones_token ON sesiones_activas(token_sesion);

-- === CONFIGURACIÓN GLOBAL (fila única, id=1) ===
CREATE TABLE configuracion_sistema (
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
INSERT INTO configuracion_sistema (id) VALUES (1);

-- === ALTAS PENDIENTES (autoregistro vía Google) ===
CREATE TABLE solicitudes_registro (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    nombre_completo TEXT NOT NULL DEFAULT '',
    creado_en TIMESTAMP NOT NULL DEFAULT NOW()
);

-- === SUCURSALES (puntos de origen) ===
CREATE TABLE sucursales (
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
CREATE TABLE flota_vehiculos (
    id SERIAL PRIMARY KEY,
    patente_identificador TEXT NOT NULL,
    marca_modelo TEXT NOT NULL DEFAULT '',
    tipo TEXT NOT NULL DEFAULT '',
    autonomia_maxima_km DOUBLE PRECISION NOT NULL DEFAULT 0,
    capacidad_volumen_m3 DOUBLE PRECISION NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'mantenimiento', 'baja'))
);

-- === CLIENTES Y SUS DIRECCIONES ===
CREATE TABLE clientes (
    id SERIAL PRIMARY KEY,
    razon_social TEXT NOT NULL,
    cuit_rut TEXT UNIQUE NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE direcciones_cliente (
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
CREATE TABLE tipos_documentos (
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
    ('odt', 'ODT', TRUE, FALSE, FALSE);

-- === FLETES Y SUS PARADAS ===
CREATE TABLE fletes (
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
CREATE INDEX idx_fletes_estado ON fletes(estado);

CREATE TABLE documentos_flete (
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
CREATE INDEX idx_documentos_flete_flete_id ON documentos_flete(flete_id);

-- === USUARIO ADMIN INICIAL ===
-- Sin esto, un despliegue nuevo queda sin forma de crear el primer usuario: todos los endpoints
-- de alta de usuarios (/api/usuarios, /api/admin/solicitudes/procesar) requieren ya estar logueado
-- como admin. Se genera una clave aleatoria (no queda ninguna clave fija en el código ni en este
-- archivo) y se imprime UNA sola vez en el log del contenedor de la base de datos al iniciar
-- (ver con: docker compose logs db).
DO $$
DECLARE
    temp_password TEXT := encode(gen_random_bytes(9), 'base64');
BEGIN
    INSERT INTO usuarios (username, password_hash, nombre_completo, rol, activo)
    VALUES ('admin', crypt(temp_password, gen_salt('bf')), 'Administrador Inicial', 'admin', TRUE)
    ON CONFLICT (username) DO NOTHING;

    RAISE NOTICE '=================================================================';
    RAISE NOTICE 'JZ Travel - usuario admin inicial creado.';
    RAISE NOTICE 'Usuario: admin | Clave temporal: %', temp_password;
    RAISE NOTICE 'Guardala ahora (no se vuelve a mostrar) y cambiala tras el primer login.';
    RAISE NOTICE '=================================================================';
END $$;
