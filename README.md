# JZTravell

Sistema de gestión de transporte (TMS) para empresas de logística y fletes: flota,
sucursales, clientes, despacho y consolidación de fletes, seguimiento GPS de choferes e
impresión de hojas de ruta. Parte de **JZTech Suite**. Software libre, pensado para correr
en una PC de oficina común.

- [Funcionalidades](#funcionalidades)
- [Detalle técnico](#detalle-técnico)
- [Instalación rápida con Docker](#instalación-rápida-con-docker)
- [Instalación en el servidor (sin Docker)](#instalación-en-el-servidor-sin-docker)
- [Acceso desde la red (HTTPS)](#acceso-desde-la-red-https)
- [Configuración](#configuración)
- [Operación](#operación)
- [Regenerar el lockfile](#regenerar-el-lockfile)
- [Cambios](#cambios)
- [Contribuir y licencia](#contribuir-y-licencia)

---

## Funcionalidades

Tres roles, cada uno con su propia pantalla (el login redirige a la que corresponde):

| Rol | Pantalla | Qué puede hacer |
|---|---|---|
| `admin` | `admin.html` | Tablero de fletes por estado; alta y edición de usuarios, vehículos, sucursales y clientes; despacho de fletes; aprobación de altas pedidas por Google; configuración del sistema. |
| `chofer` | `chofer.html` | Ver los envíos pendientes, consolidarlos en una ruta optimizada, cambiar su estado y enviar su posición GPS mientras trabaja. Abre la ruta en Google Maps. |
| `cliente` | `cliente.html` | Pedir fletes y seguir el estado de los propios. |

Otras funciones: hoja de ruta imprimible (`/api/fletes/imprimir`), tipos de documento por flete
(remito, factura, etc.) e ingreso opcional con cuenta de Google.

---

## Detalle técnico

### Stack

| Componente | Versión |
|---|---|
| Python | 3.11 a 3.13 (Docker: imagen `python:3.11-slim-bookworm`; sin Docker: el del sistema) |
| FastAPI / Starlette | 0.141.1 / 1.7.0 |
| Uvicorn | 0.54.0 |
| asyncpg | 0.31.0 (SQL directo, sin ORM) |
| PostgreSQL | 15 (Docker, imagen `postgres:15-alpine`); sin Docker, el del sistema (15 a 17) |
| [jztech-core](https://github.com/Jonnyonz/jztech-core) | 0.1.5 (librería de seguridad común de JZTech) |

Todas las dependencias están fijadas con hash en `backend/requirements.txt` (se instalan con
`pip install --require-hashes`).

### Estructura

```
JZTravell/
├── backend/
│   ├── main.py            # App FastAPI: lifespan, middlewares, routers, estáticos
│   ├── database.py        # Pool de Postgres, esquema, sesiones, roles, claves, rate limit
│   ├── routers/           # Un archivo por dominio (todas las rutas bajo /api)
│   │   ├── auth.py        # login, logout, configuración inicial, Google SSO
│   │   ├── users.py       # usuarios y solicitudes de alta
│   │   ├── config.py      # configuración del sistema
│   │   ├── fleet.py       # vehículos
│   │   ├── branches.py    # sucursales
│   │   ├── clients.py     # clientes y sus direcciones
│   │   └── shipments.py   # fletes, consolidación, estados, GPS, impresión
│   ├── requirements.in    # dependencias directas
│   ├── requirements.txt   # lockfile con hashes (generado, no editar a mano)
│   └── Dockerfile
├── frontend/              # index.html (login), admin.html, chofer.html, cliente.html
├── init_db.sql            # esquema y datos iniciales de la base
├── docker-compose.yml
├── install.sh             # instalador con Docker
└── .env.example
```

### Modelo de datos

`init_db.sql` crea el esquema (usa la extensión `pgcrypto` para los UUID) y carga la fila de
configuración y los tipos de documento. Tablas principales:

| Tabla | Contenido |
|---|---|
| `usuarios` | Usuarios, rol, hash de clave, bloqueo por intentos, última posición GPS |
| `sesiones_activas` | Sesiones abiertas (solo el hash del token) |
| `solicitudes_registro` | Altas pedidas desde Google, pendientes de aprobación |
| `configuracion_sistema` | Una sola fila (`id=1`) con la configuración editable desde el panel |
| `sucursales`, `flota_vehiculos` | Sucursales (con coordenadas) y vehículos |
| `clientes`, `direcciones_cliente` | Clientes y sus direcciones de entrega |
| `fletes`, `documentos_flete`, `tipos_documentos` | Fletes, documentos asociados y sus tipos |
| `login_rate_limit` | Intentos de login fallidos por IP (la crea la app al arrancar) |

### Seguridad

- **Sesiones opacas en base:** al iniciar sesión se genera un token aleatorio que viaja en una
  cookie `HttpOnly` + `Secure` + `SameSite=Lax` (8 horas). En la base se guarda solo su hash
  SHA-256, así que una copia de la base no permite robar sesiones. El logout la borra.
- **Claves con Argon2id** (parámetros mínimos de OWASP: 19 MiB, t=2, p=1). Las cuentas con
  hashes bcrypt de versiones anteriores se migran solas en el primer login correcto.
- **Denegar por defecto:** toda ruta de la API exige sesión salvo una lista explícita de
  públicas (login, configuración inicial, Google SSO y `GET /api/config`). Los permisos por
  rol se validan en el servidor; la cookie `user_rol` es solo para la interfaz.
- **CSRF:** todo `POST`/`PUT`/`DELETE` tiene que mandar la cabecera `X-CSRF-Token` igual a la
  cookie `csrf_token` (double-submit).
- **Fuerza bruta:** límite de intentos fallidos por IP y bloqueo de la cuenta por 15 minutos
  tras 5 intentos. La IP real se toma de `X-Forwarded-For` solo si la conexión viene de un
  proxy listado en `TRUSTED_PROXIES`.
- **Cabeceras:** CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy` y
  `Permissions-Policy` (geolocalización habilitada solo para el propio sitio, por el GPS de
  los choferes; cámara y micrófono bloqueados).
- **Configuración inicial con token:** el primer administrador solo se puede crear con el
  `SETUP_TOKEN` del `.env`, y una sola vez.
- **Google SSO** con parámetro `state` contra CSRF de login. Un email desconocido no entra:
  queda como solicitud hasta que un admin la apruebe.

---

## Instalación rápida con Docker

Es el camino recomendado. Requisitos: Linux con Docker y el plugin `docker compose`, `git` y
`openssl`. También funciona en Windows con Docker Desktop.

### Opción A: instalador

```bash
git clone https://github.com/Jonnyonz/JZTravell.git
cd JZTravell
./install.sh
```

`install.sh` genera un `.env` con clave de base y `SETUP_TOKEN` aleatorios (si ya existe uno,
lo respeta), levanta los contenedores y al final muestra la URL y el `SETUP_TOKEN`.

### Opción B: a mano

```bash
git clone https://github.com/Jonnyonz/JZTravell.git
cd JZTravell
cp .env.example .env
# Editar .env: poner una POSTGRES_PASSWORD propia y un SETUP_TOKEN aleatorio, por ejemplo:
#   openssl rand -hex 16   (clave de la base)
#   openssl rand -hex 24   (SETUP_TOKEN)
docker compose up -d --build
```

### Primer ingreso

1. Abrir `http://localhost:8000` (o el puerto definido en `PORT`).
2. La pantalla detecta que no hay usuarios y pide el **token de instalación**: pegar el
   `SETUP_TOKEN` del `.env`.
3. Completar usuario, nombre y clave del administrador (mínimo 8 caracteres, con al menos una
   mayúscula y un número). El token deja de servir una vez creado el admin.
4. Desde el panel, cargar sucursales, vehículos, clientes y el resto de los usuarios.

> Las cookies de sesión son `Secure`: por `http://` el login solo funciona entrando por
> `localhost`. Para usarlo desde otras máquinas de la red hace falta HTTPS (ver
> [Acceso desde la red](#acceso-desde-la-red-https)).

---

## Instalación en el servidor (sin Docker)

Para servidores sin Docker: Debian 12/13 o Ubuntu 24.04. El instalador deja JZTravell como servicio del
sistema, con PostgreSQL del servidor y **Caddy con HTTPS** delante (la sesión usa cookies `Secure`: sin HTTPS no
se puede ingresar desde otra PC, celular o tablet).

```bash
git clone https://github.com/Jonnyonz/JZTravell.git
cd JZTravell
sudo ./install-native.sh                                       # red interna: HTTPS por la IP del servidor
sudo JZTRAVELL_DOMAIN=fletes.suempresa.com ./install-native.sh   # con dominio: certificado automatico
```

Al terminar muestra la dirección (`https://<IP>` o `https://<dominio>`) y el `SETUP_TOKEN` para crear el
administrador. Sin dominio, el certificado lo firma la CA local de Caddy: el navegador avisa "la conexión no
es privada" hasta que se instala en cada equipo el certificado raíz que indica el instalador
(`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`). En el celular del chofer el GPS del
navegador solo funciona por HTTPS.

Queda así: código y su venv en `/opt/jztravell/releases/<versión>` (la versión es el commit), enlace
`/opt/jztravell/current` a la que está en uso, configuración en `/etc/jztravell/jztravell.env`
(`root:jztravell`, 0640), servicio `jztravell` (uvicorn en `127.0.0.1:8010`, usuario de sistema sin login,
código de solo lectura) y base `jztravell_db` con su propio rol; el esquema (`init_db.sql`) se carga solo en
una base nueva. Las dependencias se instalan sin compilador, verificando los hashes. Se puede volver a correr:
no pisa los secretos ni lo agregado a mano al `.env`. Si el puerto 80 lo usa otro programa (por ejemplo
Apache), Caddy atiende solo el 443. Opciones: `JZTRAVELL_IP`, `JZTRAVELL_PORT`, `JZTRAVELL_CADDY=0` (si ya hay
otro proxy HTTPS) y `JZTRAVELL_REPO_URL`. Si en el mismo servidor sin dominio ya hay otra app de JZTech en
`https://<IP>`, usar un dominio (o nombre interno) para cada una. Es independiente de la instalación con
Docker: no se pueden usar las dos en el mismo puerto.

**Actualizar:**

```bash
sudo jztravell-actualizar            # trae la ultima version, respalda la base y cambia
sudo jztravell-actualizar --buscar   # solo dice si hay una version nueva
sudo jztravell-actualizar --volver   # vuelve a la version anterior (y a la base de antes, si cambio)
```

Arma la versión nueva aparte (si algo falla ahí, no se cambia nada), respalda la base en
`/var/backups/jztravell/`, cambia y verifica que responda. Si la versión nueva no responde, vuelve sola a la
anterior y, si el esquema de la base cambió, la restaura como estaba.

### Para desarrollo (a mano)

```bash
# 1. Paquetes del sistema
sudo apt install -y python3 python3-venv postgresql git openssl

# 2. Base de datos (reemplazar CLAVE por una propia: openssl rand -hex 16)
sudo -u postgres psql -c "CREATE USER jzadmin WITH PASSWORD 'CLAVE';"
sudo -u postgres psql -c "CREATE DATABASE jzflete_db OWNER jzadmin;"

# 3. Código y esquema
git clone https://github.com/Jonnyonz/JZTravell.git
cd JZTravell
PGPASSWORD='CLAVE' psql -h 127.0.0.1 -U jzadmin -d jzflete_db -f init_db.sql

# 4. Entorno de Python
python3 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r backend/requirements.txt

# 5. Variables (la app no lee el .env sola: exportarlas o usar un EnvironmentFile de systemd)
export POSTGRES_HOST=127.0.0.1 POSTGRES_USER=jzadmin POSTGRES_PASSWORD='CLAVE' POSTGRES_DB=jzflete_db
export SETUP_TOKEN=$(openssl rand -hex 24); echo "SETUP_TOKEN: $SETUP_TOKEN"

# 6. Arrancar (desde backend/: el frontend se sirve desde ../frontend)
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000
```

Después seguir con el [primer ingreso](#primer-ingreso). Para dejarlo como servicio, crear una
unidad de `systemd` con esas variables en un `EnvironmentFile` (permisos `0640`) y poner un
proxy con HTTPS adelante.

---

## Acceso desde la red (HTTPS)

Para entrar desde otras PCs o desde el celular de los choferes hace falta HTTPS, porque las
cookies de sesión son `Secure` y el navegador solo comparte la ubicación GPS en sitios
seguros. La forma más simple es [Caddy](https://caddyserver.com/) como proxy inverso en el
mismo servidor:

```
# /etc/caddy/Caddyfile
jztravell.miempresa.com {
    reverse_proxy 127.0.0.1:8000
}
```

Con un dominio público, Caddy obtiene el certificado solo. En una red interna sin dominio se
puede usar `tls internal` (certificado de una CA local, que hay que instalar en cada equipo).

Con el proxy delante, conviene publicar la app solo en la máquina local: en
`docker-compose.yml`, cambiar `"${PORT:-8000}:8000"` por `"127.0.0.1:${PORT:-8000}:8000"`.

---

## Configuración

### Variables de entorno (`.env`)

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `POSTGRES_USER` | Sí | `jzadmin` | Usuario de la base. |
| `POSTGRES_PASSWORD` | Sí | — | Clave de la base. Generarla con `openssl rand -hex 16`. Postgres la toma solo la primera vez que crea sus datos (`postgres-data/`): si se cambia después, hay que cambiarla también dentro de la base. |
| `POSTGRES_DB` | Sí | `jzflete_db` | Nombre de la base. |
| `SETUP_TOKEN` | Sí (para la primera vez) | vacío | Token para crear el primer administrador. Vacío = la configuración inicial queda deshabilitada. Generarlo con `openssl rand -hex 24`. |
| `PORT` | No | `8000` | Puerto donde Docker publica la app. |
| `PROJECT_NAME` | No | `JZ_Travel` | Informativa, la app no la usa. |
| `TRUSTED_PROXIES` | No | `127.0.0.1/32,::1/128,172.16.0.0/12` | Proxies de confianza (IPs o redes, separadas por coma). Solo de ellos se acepta `X-Forwarded-For` para saber la IP real del cliente (rate limit del login). |
| `POSTGRES_HOST` | No | `db` | Host de Postgres. En Docker lo fija el compose; en instalación local, `127.0.0.1`. |

> `TRUSTED_PROXIES` está en `.env.example`, pero hoy el `docker-compose.yml` no se la pasa al
> contenedor, así que en Docker siempre rige el default. El default ya cubre un proxy en el
> mismo servidor.

### Configuración desde el panel

En **Configuración** del panel de admin (se guarda en la tabla `configuracion_sistema`):

| Campo | Para qué sirve |
|---|---|
| Nombre de la empresa | Encabezado de la hoja de ruta impresa (si está vacío: "Mi Empresa"). |
| Frecuencia de GPS (segundos) | Cada cuánto `chofer.html` envía la posición del chofer (10 si no se configura). |
| Ciudad, provincia y país por defecto | Valores precargados en las altas de direcciones. |
| Manejar volúmenes | Agrega el campo "Volumen requerido (m³)" al alta de fletes. |
| Google SSO | Activación, Client ID, Client Secret y URL de redirección. |

### Ingreso con Google (opcional)

1. En Google Cloud Console, crear credenciales OAuth 2.0 de tipo "Aplicación web".
2. Como URI de redirección autorizada, cargar `https://<tu-dominio>/api/auth/google/callback`.
3. En el panel de JZTravell, activar Google SSO y cargar el Client ID, el Client Secret y
   exactamente esa misma URL de redirección. El botón "Ingresar con Google" aparece en el login
   solo cuando está activado.
4. Cuando alguien entra con un email que no existe como usuario, queda en
   **Solicitudes**: el admin la aprueba y le asigna un rol.

El Client Secret se guarda en la base y nunca se devuelve por la API.

---

## Operación

```bash
# Ver el estado y los logs
docker compose ps
docker compose logs -f backend

# Actualizar a la última versión
git pull
docker compose up -d --build

# Backup y restauración de la base
docker compose exec -T db pg_dump -U jzadmin jzflete_db > backup_$(date +%F).sql
docker compose exec -T db psql -U jzadmin -d jzflete_db < backup_AAAA-MM-DD.sql

# Detener (los datos quedan en ./postgres-data)
docker compose down
```

Los datos de Postgres viven en la carpeta `./postgres-data` del proyecto: incluirla (o el
`pg_dump`) en las copias de seguridad.

---

## Regenerar el lockfile

Al cambiar `requirements.in`, el lockfile se regenera siempre en Linux, dentro de
la misma imagen base del Dockerfile. En Windows `pip-compile` resuelve las dependencias propias
de Windows y el build de Docker falla.

```bash
cd backend
docker run --rm -v "$PWD:/w" -w /w python:3.11-slim-bookworm sh -c \
  "pip install pip-tools==7.4.1 && pip-compile --allow-unsafe --generate-hashes \
   --output-file=requirements.txt requirements.in"
```

---

## Cambios

Lo que cambia en cada actualización está en `CHANGELOG.md`.

---

## Contribuir y licencia

Las contribuciones son bienvenidas: ver `CONTRIBUTING.md`. Cada commit tiene que llevar `Signed-off-by`
(`git commit -s`, Developer Certificate of Origin), ser un único cambio y estar
probado. Sin emojis en la interfaz: íconos solo en SVG.

Licencia: **AGPLv3** (GNU Affero General Public License v3). Ver `LICENSE`. Si ofrecés una versión modificada como servicio en red, tenés que publicar su código fuente.
