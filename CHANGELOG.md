# Notas de los parches

Cambios de JZTravell, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## Sin versión todavía (2026-10-06)

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
