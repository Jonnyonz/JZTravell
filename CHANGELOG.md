# Notas de los parches

Cambios de JZTravell, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 2026-10-05

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
