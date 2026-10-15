# Notas de los parches

Cambios de JZTravell, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 1.3.0 — 2026-10-15

Migración `0003` (se aplica sola al arrancar).

### Agregado
- **Prueba de entrega:** en cada parada el chofer registra quién recibe, la firma (con el dedo en la pantalla), una
  foto opcional (se achica en el celular) y su posición GPS. Si la dirección no tenía ubicación, se guarda la del
  GPS. El comprobante impreso muestra hora, quién recibió y la firma; el admin las ve en "Detalle".
- **Incidencias:** "No entregada" con motivo (ausente, rechazo, dirección errónea, dañado, cerrado, otro),
  observaciones y foto. Al cerrar la ruta, el flete queda en Incidencia para reprogramarlo.
- **Link de seguimiento** público por flete (`/seguimiento.html?t=...`): estado, paradas y ubicación aproximada del
  vehículo en camino, sin cuenta y sin datos personales. Lo copian el admin y el cliente.
- **Vencimientos:** VTV, seguro y habilitación de cada vehículo y licencia de cada chofer. Con alguno vencido no se
  puede salir (se controla al tomar fletes) y el tablero avisa los que vencen en los próximos 30 días.
- **Mantenimiento:** aviso de service cada tantos km e historial (service, reparación, cubiertas) con km y costo.
- **Ventanas horarias** por parada (en fletes y cotizaciones): el orden de la ruta atiende primero los horarios
  límite más tempranos.
- **Costo real:** al cerrar la ruta el chofer carga combustible, peajes y otros gastos.
- **Reportes:** fletes, entregas a tiempo, incidencias por motivo, facturación por cliente, km y mantenimiento por
  vehículo y costo real contra ingresos de cada ruta, con descarga en CSV.

### Cambiado
- El cierre de ruta del chofer pasa a una ventana con odómetro y gastos (antes, un aviso del navegador).
- La CSP permite imágenes `data:` y `blob:` (vista previa de la foto en el celular).

## 1.2.0 — 2026-10-15

Funciones nuevas para la prueba piloto. Migración `0002` (se aplica sola al arrancar).

### Agregado
- **Clientes externos:** además de las entregas propias, viajes para terceros. Un usuario con rol cliente queda
  vinculado a su empresa (Usuarios > Empresa) y desde su portal pide cotizaciones con el retiro y las entregas en
  cualquier dirección, las acepta o rechaza, sigue sus viajes y carga sus direcciones. Los fletes externos llevan
  cliente y precio; el admin también puede cargar uno directo, sin cotización.
- **Cotizaciones** (pestaña nueva): el precio sale de los km y de los costos (combustible o energía según el consumo
  de cada vehículo, desgaste por km, chofer por hora, peajes y otros gastos), más margen e IVA. El admin elige el
  vehículo, ajusta y la envía; el cliente ve km, IVA y total, sin el desglose. Al aceptarla se crea el flete.
- **Volumen por vehículo:** cada vehículo indica si trabaja por volumen y su capacidad. Reemplaza al ajuste global
  "Manejar volúmenes" (se copia a los vehículos existentes al actualizar).
- **Cambio de panel:** el admin pasa a ver el panel del chofer o del cliente y vuelve con "Volver al admin". El
  servidor sigue validando el rol real (`GET /api/me`).
- **Km recorridos sin OSRM:** al tomar fletes el chofer arma una ruta con el odómetro de salida y al cerrarla carga
  el de llegada; mientras tanto se guarda su rastro GPS y se miden los km (sin puntos imprecisos ni saltos
  imposibles). Los km estimados son línea recta x factor de calle, y el sistema sugiere el factor según los viajes
  medidos. Pestaña **Rutas** con estimado, odómetro, GPS y desvío.
- **Ubicación de sucursales y direcciones:** se pega el link de Google Maps o "latitud, longitud". Las que estaban
  en el centro de CABA por defecto pasan a "sin ubicar" (no se usan para calcular km).
- Costos en Configuración: precio del litro y del kWh, chofer por hora, velocidad promedio, minutos por parada,
  margen, IVA y factor de calle. Consumo, desgaste por km y odómetro en cada vehículo.

### Corregido
- El chofer no veía su ruta en curso: después de consolidar, sus envíos desaparecían de la pantalla.
- Agregar direcciones a un cliente desde el panel fallaba siempre (faltaba la ruta en el backend y el campo
  altura no se leía).
- Crear un flete con destino a un cliente registrado o a una dirección manual fallaba (la base solo aceptaba
  `sucursal`/`cliente` como tipo de destino).
- "Re-procesar" una incidencia dejaba el flete con el chofer anterior y nadie lo podía volver a tomar.
- Guardar la Configuración borraba el Client Secret de Google (el panel no lo puede reenviar): ahora vacío = se
  conserva.
- Las altas pedidas por Google no se mostraban en el panel (la tabla quedaba vacía).
- El portal del cliente no enviaba las solicitudes ("Solicitar Unidad" no hacía nada) y "Cerrar sesión" no cerraba
  la sesión en el servidor. El chofer no tenía botón para salir.
- Alta de usuario con un nombre repetido o un rol inválido: ahora 400 con el motivo (antes 500).
- La base en Docker usa la hora de Argentina (`TZ` en el `.env` la cambia): antes las fechas quedaban en UTC.

### Cambiado
- `POST /api/fletes` y `POST /api/clientes` quedan solo para el admin (los clientes piden por cotización).
- Frontend: funciones comunes en `frontend/js/comun.js` (pedidos con CSRF, `/api/me`, ubicación, cambio de panel).

## 1.1.1 — 2026-10-15

### Cambiado (dependencias alineadas en las cuatro apps)
- Mismas versiones en Tracker360, JZPass, JZTravell y JZ Middle: FastAPI 0.143.1 (Starlette 1.8.0), Uvicorn
  0.54.0, Pydantic 2.14.1, asyncpg 0.32.0, argon2-cffi 25.1.0, httpx 0.28.1, bcrypt 5.0.0 y `jztech-core` 0.1.6.
  Las dependencias indirectas se actualizaron a su última versión compatible. Todas tienen binarios para
  Python 3.11 a 3.13 (la instalación sin Docker sigue sin necesitar compilador).
- FastAPI agrega desde la 0.142 la dependencia `opentelemetry-api` (paquete oficial de OpenTelemetry).
- `jztech-core` 0.1.6: con bcrypt 5, una clave de más de 72 caracteres contra un hash bcrypt viejo ya no da error.
- La imagen Docker pasa a `python:3.11-slim` (la misma de las otras apps; antes `python:3.11-slim-bookworm`).
- Se saca `python-multipart` (JZTravell no recibe formularios).

## 1.1.0 — 2026-10-15

Alinea JZTravell con `jztech-core` y con Tracker360. **Al actualizar se cierran las sesiones abiertas: cada
usuario vuelve a iniciar sesión una vez.**

### Cambiado (sesiones y CSRF de jztech-core)
- Las sesiones pasan a `jztech_core.sessions` (tabla `jztech_sessions`, la misma de Tracker360 y JZPass) y
  reemplazan a `sesiones_activas`. La cookie de sesión pasa de `SameSite=Lax` a `Strict`.
- El CSRF pasa a `jztech_core.csrf`: el token es un HMAC ligado a la sesión (antes era un valor al azar que no
  dependía del usuario). Se valida al autenticar cada `POST`/`PUT`/`DELETE` y la cookie `csrf_token` la pone el
  login. El frontend no cambia: sigue mandando la cookie en el encabezado `X-CSRF-Token`.
- El token de la configuración inicial se valida con `jztech_core.setup_flow`.

### Agregado (migraciones versionadas)
- El esquema de la base lo crea y lo actualiza la app al arrancar (`jztech_core.migrations`, tabla
  `schema_version`), con los archivos de `backend/migrations/`. `0001_esquema_base.sql` es el esquema de antes,
  idempotente: sobre una base creada por una versión anterior no cambia los datos. Se borra `init_db.sql`; los
  instaladores ya no cargan el esquema a mano y Postgres ya no lo carga al crear el contenedor.

### Cambiado (imagen Docker autocontenida)
- El código y el frontend van dentro de la imagen (como en Tracker360): el compose ya no monta `./backend` ni
  `./frontend`. La app corre con un usuario sin privilegios y la imagen tiene healthcheck. `install.sh` ya
  reconstruía la imagen en cada corrida.

### Corregido (seguridad)
- Cambiar la clave de un usuario o desactivarlo cierra todas sus sesiones abiertas.
- El ingreso con Google solo acepta emails verificados por Google (`email_verified`).

### Incluye lo que no tenía versión (2026-10-06 / 2026-10-13)

### Corregido (instalador con Docker, 2026-10-13)
- `install.sh` ya no levanta los contenedores con `--remove-orphans`. En un servidor compartido, Docker puede
  contar como "huérfanos" del proyecto contenedores de otras aplicaciones con la misma etiqueta de proyecto, y los
  borraba. El Caddy de la versión anterior (`jztravel_caddy`) se sigue borrando por su nombre.

### Cambiado (instaladores sin Caddy)
- `install.sh` (Docker) e `install-native.sh` ya no levantan ni instalan Caddy: instalan JZTravell y lo dejan
  escuchando por http en su puerto (8010 en instalaciones nuevas; las existentes conservan el suyo) de todas las
  interfaces (`JZTRAVELL_BIND` lo cambia). El HTTPS lo pone el proxy del servidor. Al terminar muestran dónde quedó
  escuchando, la dirección pública (`JZTRAVELL_DOMAIN`) y el token inicial. `JZTRAVELL_PROXY_IP=<IP>` suma a
  `TRUSTED_PROXIES` la IP de un proxy que esté en otro equipo. Se saca el servicio `caddy` y su perfil `https` del
  compose; `JZTRAVELL_HTTPS`, `JZTRAVELL_IP` y `JZTRAVELL_CADDY` se ignoran con un aviso.
- Instalaciones existentes: al volver a correr el instalador con Docker se saca el contenedor `jztravel_caddy`, la
  carpeta `caddy/` y las claves que ya no se usan del `.env` (`CADDY_*`, `JZTRAVELL_HTTPS`, `JZTRAVELL_IP`,
  `COMPOSE_PROFILES=https`), y la app pasa a escuchar en todas las interfaces; los volúmenes de certificados de
  Caddy no se borran solos (el instalador muestra el `docker volume rm`). Sin Docker, un Caddy configurado por una
  versión anterior no se desinstala (puede usarlo otra app): el instalador avisa cómo sacarlo.
  `jztravell-actualizar` verifica la salud en la interfaz donde escucha el servicio.

## 1.0.0 — 2026-10-06

Primer release numerado (tag `v1.0.0`, publicado en GitHub Releases). Marca como 1.0.0 todo lo que está
abajo; desde acá cada release se numera (1.x).

## 2026-10-05

### Agregado (HTTPS en la instalación con Docker)
- `install.sh` configura HTTPS: levanta un contenedor de Caddy (`jztravel_caddy`, perfil `https` del compose)
  delante del backend. Con dominio (`JZTRAVELL_DOMAIN=fletes.empresa.com`, o contestando la pregunta) saca el
  certificado solo; sin dominio usa la IP del servidor con la CA local de Caddy y deja el certificado raíz en
  `caddy/ca-local.crt`. Si el 443 ya está en uso, queda en el primero libre entre 8443, 9443 y 10443. Al terminar
  muestra un aviso con la dirección y qué hacer con el certificado. Sin HTTPS no se podía iniciar sesión desde
  otra PC ni usar el GPS del chofer. `JZTRAVELL_HTTPS=no` lo desactiva.
- `install.sh` ahora también actualiza: trae la versión nueva (`git pull --ff-only`), respalda la base en
  `backups/` antes de reconstruir y sigue con la versión recién bajada del propio instalador.
- Una instalación nueva usa el puerto 8010 (antes 8000, el mismo que JZPass) y escucha solo en el servidor
  (se entra por Caddy). Las instalaciones existentes conservan su puerto. El compose acepta `APP_BIND` y pasa
  `TRUSTED_PROXIES` al backend.

### Seguridad (datos)
- `install.sh` (Docker) ya no borra la base: si no encontraba el `.env` pero quedaba la carpeta de la base de
  una instalación anterior (`postgres-data`), la eliminaba sin preguntar. Ahora se detiene sin tocar nada y
  explica las opciones: restaurar el `.env`, o empezar de cero a propósito con `JZTRAVELL_RESET_DB=1`.

### Agregado (instalación sin Docker)
- Instalador para servidores sin Docker: `sudo ./install-native.sh` (Debian 12/13, Ubuntu 24.04). Deja JZTravell
  como servicio del sistema (`jztravell`, usuario propio sin login, código de solo lectura), con su base y rol en
  el PostgreSQL del servidor (carga `init_db.sql` solo en una base nueva) y Caddy con HTTPS delante: con
  `JZTRAVELL_DOMAIN` saca el certificado solo; sin dominio usa la IP del servidor con la CA local de Caddy. Sin
  HTTPS la sesión no se guarda (cookies `Secure`) y el GPS del chofer no funciona. Instala las dependencias sin
  compilar, verificando los hashes, y se puede volver a correr sin pisar secretos ni configuración agregada a mano.
- Actualizador `sudo jztravell-actualizar` (`--buscar`, `--volver`): arma la versión nueva aparte, respalda la
  base, cambia y verifica que responda; si no responde, vuelve solo a la versión anterior y, si el esquema de la
  base cambió, la restaura como estaba.
- `.gitattributes`: los scripts de Linux siempre con finales de línea LF.
- README: la tabla de versiones refleja las dependencias actuales.

### Cambiado (dependencias)
- Versiones alineadas con Tracker360, JZ Middle ML-Tracker y JZPass: FastAPI 0.141.1 (Starlette 1.7),
  uvicorn 0.54, pydantic 2.13.5, asyncpg 0.31, python-multipart 0.0.32 y httpx 0.28.1. Las anteriores no tenían
  paquetes binarios para Python 3.13 (el de Debian 13), así que no se podían instalar sin compilar fuera de
  Docker. uvicorn deja de usar el extra `[standard]` (seis paquetes compilados que no hacían falta). Sin cambios
  para el usuario; la imagen de Docker sigue en Python 3.11.

## 2026-09-30

### Cambiado
- Los tests automáticos ya no forman parte del repositorio: se mantienen aparte, fuera del
  código del sistema. No cambia nada del funcionamiento.
