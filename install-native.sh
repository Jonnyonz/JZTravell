#!/bin/bash
# ==============================================================================
# Instalador nativo (sin Docker) de JZTravell
# ==============================================================================
# Para Debian 12/13 y Ubuntu 24.04 (apt, Python 3.11 o mas nuevo). Correr como root desde la raiz de un
# clon del repositorio:
#
#   sudo ./install-native.sh
#   sudo JZTRAVELL_DOMAIN=fletes.cliente.com ./install-native.sh   dominio publico con el que se va a entrar
#
# Queda asi:
#   /opt/jztravell/src/                 clon de git del que se actualiza (lo usa jztravell-actualizar)
#   /opt/jztravell/releases/<version>/  codigo + su propio venv (una carpeta por version; version = commit)
#   /opt/jztravell/current              enlace a la version en uso (el actualizador lo cambia)
#   /etc/jztravell/jztravell.env        configuracion y secretos (root:jztravell, 0640)
#   servicio systemd "jztravell"        uvicorn en 0.0.0.0:8010, un worker
#   base "jztravell_db" y rol "jztravell" propios en el PostgreSQL del servidor (esquema: init_db.sql)
#   Sin proxy propio (sin Caddy): el HTTPS lo pone el proxy del servidor, que reenvia el dominio al puerto
#   del servicio (la sesion usa cookies Secure: sin HTTPS no se puede ingresar desde otra PC)
#   /usr/local/sbin/jztravell-actualizar  actualizador (respaldo, chequeo y vuelta atras)
#
# Idempotente: se puede volver a correr. Los secretos ya generados no se pisan y el esquema se carga solo en
# una base nueva. Sin compilador: las dependencias se instalan solo con paquetes binarios (wheels) verificando
# los hashes de backend/requirements.txt; si hay una carpeta wheelhouse/ al lado, se usa esa (sin internet).
#
# Es independiente de la instalacion con Docker (install.sh): no se pueden usar las dos en el mismo puerto.
#
# Variables opcionales:
#   JZTRAVELL_DOMAIN=fletes.cliente.com  dominio publico con el que se entra
#   JZTRAVELL_BIND=0.0.0.0               interfaz donde escucha el servicio (el proxy tiene que llegar)
#   JZTRAVELL_PORT=8010                  puerto del servicio
#   JZTRAVELL_PROXY_IP=192.168.1.5       IP del proxy si esta en otro equipo (se suma a TRUSTED_PROXIES)
#   JZTRAVELL_REPO_URL=...               repositorio del que se actualiza (por defecto, el origin de este clon)
# ==============================================================================

set -euo pipefail

APP_NAME="jztravell"
APP_USER="jztravell"
BASE_DIR="${JZTRAVELL_DIR:-/opt/jztravell}"
RELEASES="$BASE_DIR/releases"
SRC="$BASE_DIR/src"
ENV_DIR="/etc/$APP_NAME"
ENV_FILE="$ENV_DIR/$APP_NAME.env"
SERVICE="$APP_NAME"
DB_NAME="jztravell_db"
DB_USER="jztravell"
ACTUALIZADOR="/usr/local/sbin/jztravell-actualizar"
TRUSTED_DEFAULT="127.0.0.1/32,::1/128,172.16.0.0/12"   # el mismo default que la app (backend/database.py)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /   # psql como postgres no puede entrar a la carpeta desde la que se corre (por ejemplo /root)

valor_env() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 || true; }

echo "=================================================="
echo "Instalador nativo de JZTravell"
echo "=================================================="

# 1. Privilegios, sistema y ubicacion
if [ "$EUID" -ne 0 ]; then
  echo "Error: correr como root (sudo ./install-native.sh)." >&2
  exit 1
fi
if [ ! -f /etc/debian_version ]; then
  echo "Error: este instalador es para Debian/Ubuntu (apt)." >&2
  exit 1
fi
if [ ! -f "$SCRIPT_DIR/backend/main.py" ] || [ ! -f "$SCRIPT_DIR/backend/requirements.txt" ] || [ ! -f "$SCRIPT_DIR/init_db.sql" ]; then
  echo "Error: correr el script desde la raiz del repo (faltan backend/main.py, backend/requirements.txt o init_db.sql)." >&2
  exit 1
fi

# Configuracion: lo pedido, lo de la instalacion anterior o el valor por defecto.
APP_PORT="${JZTRAVELL_PORT:-$(valor_env APP_PORT)}"; APP_PORT="${APP_PORT:-8010}"
DOMAIN="${JZTRAVELL_DOMAIN:-$(valor_env JZTRAVELL_DOMAIN)}"
DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%/}"
# Hasta la version con Caddy el servicio escuchaba solo en 127.0.0.1: ahora el proxy tiene que poder llegar.
APP_BIND="${JZTRAVELL_BIND:-$(valor_env JZTRAVELL_BIND)}"; APP_BIND="${APP_BIND:-0.0.0.0}"
PROXY_IP="${JZTRAVELL_PROXY_IP:-}"
for VIEJA in JZTRAVELL_IP JZTRAVELL_CADDY; do
  if [ -n "${!VIEJA:-}" ]; then echo "Aviso: $VIEJA ya no se usa (ya no hay Caddy); se ignora."; fi
done
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "Error: dominio invalido: $DOMAIN" >&2
  exit 1
fi
if ! [[ "$APP_PORT" =~ ^[0-9]+$ ]]; then
  echo "Error: puerto invalido: $APP_PORT" >&2
  exit 1
fi
if ! [[ "$APP_BIND" =~ ^[0-9A-Fa-f.:]+$ ]]; then
  echo "Error: JZTRAVELL_BIND invalido: $APP_BIND" >&2
  exit 1
fi
if [ -n "$PROXY_IP" ] && ! [[ "$PROXY_IP" =~ ^[0-9A-Fa-f.:]+(/[0-9]+)?$ ]]; then
  echo "Error: JZTRAVELL_PROXY_IP invalida: $PROXY_IP" >&2
  exit 1
fi

# 2. Paquetes del sistema (sin compilador ni cabeceras de Python)
echo "Instalando paquetes del sistema..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv postgresql postgresql-client openssl curl rsync git ca-certificates \
  iproute2 > /dev/null
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Error: hace falta Python 3.11 o mas nuevo (este sistema tiene $(python3 --version 2>&1))." >&2
  echo "Sistemas soportados: Debian 12, Debian 13, Ubuntu 24.04." >&2
  exit 1
fi
IP_SERVIDOR="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -z "$IP_SERVIDOR" ]; then
  IP_SERVIDOR="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
fi
IP_SERVIDOR="${IP_SERVIDOR:-<IP del servidor>}"
if [ "$APP_BIND" = "0.0.0.0" ] || [ "$APP_BIND" = "::" ]; then
  DESTINO="$IP_SERVIDOR"; LOCAL="127.0.0.1"
else
  DESTINO="$APP_BIND"; LOCAL="$APP_BIND"
fi

# El puerto local tiene que estar libre (por ejemplo, ocupado por la instalacion con Docker).
OCUPANTE="$(ss -ltnpH "( sport = :$APP_PORT )" 2>/dev/null || true)"
if [ -n "$OCUPANTE" ] && ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  echo "Error: el puerto $APP_PORT ya esta en uso:" >&2
  echo "$OCUPANTE" >&2
  echo "Si es JZTravell con Docker (install.sh), no se pueden usar las dos instalaciones en el mismo puerto." >&2
  echo "Usar otro puerto con JZTRAVELL_PORT=8011 o detener la de Docker (docker compose down)." >&2
  exit 1
fi

# 3. Usuario de sistema sin login (no es dueno del codigo: solo lo lee)
if ! id "$APP_USER" &> /dev/null; then
  echo "Creando usuario de sistema $APP_USER..."
  useradd --system --no-create-home --home-dir "$BASE_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# 4. Version (commit de git) y clon del que se actualiza
GIT="git -c safe.directory=*"
if $GIT -C "$SCRIPT_DIR" rev-parse --git-dir > /dev/null 2>&1; then
  VERSION="$($GIT -C "$SCRIPT_DIR" rev-parse --short=12 HEAD)"
  if [ -n "$($GIT -C "$SCRIPT_DIR" status --porcelain --untracked-files=no)" ]; then
    VERSION="$VERSION-local"   # con cambios sin commit: se instala igual, pero se marca
  fi
  REPO_URL="${JZTRAVELL_REPO_URL:-$($GIT -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)}"
else
  VERSION="local-$(date +%Y%m%d%H%M%S)"
  REPO_URL="${JZTRAVELL_REPO_URL:-}"
fi
REPO_URL="${REPO_URL:-$(valor_env JZTRAVELL_REPO_URL)}"
REPO_URL="${REPO_URL:-https://github.com/Jonnyonz/JZTravell.git}"
echo "Version a instalar: $VERSION"
mkdir -p "$BASE_DIR"
if [ ! -d "$SRC/.git" ]; then
  echo "Clonando $REPO_URL en $SRC (de ahi se actualiza)..."
  if ! $GIT clone --quiet "$REPO_URL" "$SRC"; then
    echo "Aviso: no se pudo clonar $REPO_URL; jztravell-actualizar no va a funcionar hasta que exista $SRC." >&2
  fi
fi

# 5. Codigo y entorno virtual de esta version
DEST="$RELEASES/$VERSION"
echo "Instalando la version $VERSION en $DEST..."
mkdir -p "$RELEASES"
rsync -a --delete --exclude '.git' --exclude 'venv' --exclude '.venv' --exclude 'wheelhouse' --exclude 'backups' \
  --exclude '__pycache__' --exclude '.env' --exclude 'postgres-data' --exclude 'tests' "$SCRIPT_DIR"/ "$DEST"/
echo "$VERSION" > "$DEST/.jztravell-version"
if [ ! -x "$DEST/venv/bin/python" ]; then
  python3 -m venv "$DEST/venv"
fi
PIP_ORIGEN=()
if [ -d "$SCRIPT_DIR/wheelhouse" ]; then
  echo "Usando wheelhouse/ (sin internet)."
  PIP_ORIGEN=(--no-index --find-links "$SCRIPT_DIR/wheelhouse")
fi
"$DEST/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes --only-binary=:all: \
  "${PIP_ORIGEN[@]}" -r "$DEST/backend/requirements.txt"
chown -R root:root "$DEST"
chmod -R a+rX,go-w "$DEST"

# 6. PostgreSQL: rol y base propios en el cluster del servidor
echo "Verificando PostgreSQL..."
systemctl enable --now postgresql > /dev/null
ROL_EXISTE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")
BASE_EXISTE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")
if [ -f "$ENV_FILE" ]; then
  echo "Ya existe $ENV_FILE: se reutilizan los secretos (no se pisan)."
  DB_PASSWORD="$(valor_env POSTGRES_PASSWORD)"
  SETUP_TOKEN="$(valor_env SETUP_TOKEN)"
  if [ "$ROL_EXISTE" != "1" ]; then
    echo "Error: $ENV_FILE existe pero el rol $DB_USER no existe en PostgreSQL. Revisar a mano." >&2
    exit 1
  fi
else
  if [ "$ROL_EXISTE" = "1" ]; then
    echo "Error: el rol $DB_USER ya existe en PostgreSQL pero no hay $ENV_FILE con su clave." >&2
    echo "No se genera una clave nueva porque romperia el acceso existente. Revisar a mano." >&2
    exit 1
  fi
  echo "Generando secretos..."
  DB_PASSWORD="$(openssl rand -hex 24)"
  SETUP_TOKEN="$(openssl rand -hex 24)"
fi
if [ "$ROL_EXISTE" != "1" ]; then
  echo "Creando rol $DB_USER..."
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';"
fi
if [ "$BASE_EXISTE" != "1" ]; then
  echo "Creando base $DB_NAME..."
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
fi
# Esquema: init_db.sql no se puede volver a correr (crea tablas y carga datos iniciales), asi que se carga
# solo si la base todavia no lo tiene. Se carga como el rol de la app para que sea duena de las tablas;
# la extension pgcrypto la crea antes el superusuario.
TIENE_ESQUEMA=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT to_regclass('public.usuarios') IS NOT NULL")
if [ "$TIENE_ESQUEMA" != "t" ]; then
  echo "Creando el esquema de la base (init_db.sql)..."
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
  PGPASSWORD="$DB_PASSWORD" psql -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" < "$DEST/init_db.sql" > /dev/null
fi

# 7. Configuracion (se reescribe con los mismos secretos; root:jztravell 0640)
echo "Escribiendo $ENV_FILE..."
mkdir -p "$ENV_DIR"
ADICIONALES=""
if [ -f "$ENV_FILE" ]; then
  # Lo que el administrador agrego a mano se conserva.
  # JZTRAVELL_IP era de la version con Caddy: se descarta.
  ADICIONALES="$(grep -Ev '^(#|$|POSTGRES_|SETUP_TOKEN=|TRUSTED_PROXIES=|APP_PORT=|JZTRAVELL_(DOMAIN|IP|BIND|INSTALACION|REPO_URL)=|PYTHONDONTWRITEBYTECODE=|TZ=)' "$ENV_FILE" || true)"
fi
# Proxies de confianza: los de antes (el default viejo, solo localhost, pasa al de la app: un proxy en Docker en
# este mismo servidor llega desde la red de Docker) mas la IP del proxy si esta en otro equipo.
PROXIES="$(valor_env TRUSTED_PROXIES)"
if [ -z "$PROXIES" ] || [ "$PROXIES" = "127.0.0.1/32,::1/128" ]; then PROXIES="$TRUSTED_DEFAULT"; fi
if [ -n "$PROXY_IP" ] && ! tr ',' '\n' <<< "$PROXIES" | tr -d ' ' | grep -qxF "$PROXY_IP"; then
  PROXIES="$PROXIES,$PROXY_IP"
  echo "Se agrega $PROXY_IP a TRUSTED_PROXIES."
fi
ZONA="$(valor_env TZ)"; ZONA="${ZONA:-${TZ:-America/Argentina/Buenos_Aires}}"
TMP_ENV="$(mktemp "$ENV_DIR/.env.XXXXXX")"
cat > "$TMP_ENV" <<EOF
# Generado por install-native.sh (se vuelve a escribir en cada instalacion; las lineas agregadas a mano
# al final se conservan). No versionar ni copiar a otro servidor tal cual.
POSTGRES_HOST=127.0.0.1
POSTGRES_DB=$DB_NAME
POSTGRES_USER=$DB_USER
POSTGRES_PASSWORD=$DB_PASSWORD
SETUP_TOKEN=$SETUP_TOKEN
TRUSTED_PROXIES=$PROXIES
APP_PORT=$APP_PORT
JZTRAVELL_BIND=$APP_BIND
JZTRAVELL_DOMAIN=$DOMAIN
JZTRAVELL_INSTALACION=nativa
JZTRAVELL_REPO_URL=$REPO_URL
TZ=$ZONA
PYTHONDONTWRITEBYTECODE=1
EOF
if [ -n "$ADICIONALES" ]; then
  printf '%s\n' "$ADICIONALES" >> "$TMP_ENV"
fi
chown root:"$APP_USER" "$TMP_ENV"
chmod 640 "$TMP_ENV"
mv -f "$TMP_ENV" "$ENV_FILE"

# 8. Version en uso y servicio systemd
ln -sfn "$DEST" "$BASE_DIR/current.tmp"
mv -Tf "$BASE_DIR/current.tmp" "$BASE_DIR/current"

echo "Escribiendo el servicio systemd..."
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=JZTravell (TMS / fletes)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
# La app se ejecuta desde backend/ y sirve el frontend desde ../frontend.
WorkingDirectory=$BASE_DIR/current/backend
EnvironmentFile=$ENV_FILE
ExecStart=$BASE_DIR/current/venv/bin/uvicorn main:app --host $APP_BIND --port $APP_PORT --workers 1 --no-proxy-headers
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
MemoryMax=256M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" > /dev/null
systemctl restart "$SERVICE"

echo "Esperando que el servicio responda..."
OK=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 5 "http://$LOCAL:$APP_PORT/api/setup/status" > /dev/null 2>&1; then
    OK=1
    break
  fi
  sleep 2
done
if [ "$OK" != "1" ]; then
  echo "Error: el servicio no responde en http://$LOCAL:$APP_PORT." >&2
  echo "Ver el detalle con: journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi
echo "Servicio en marcha (version $VERSION)."

# 9. Actualizador
install -m 0755 "$DEST/tools/jztravell-actualizar" "$ACTUALIZADOR"

# 10. Caddy de una version anterior de este instalador: no se desinstala (puede atender otras apps), se avisa.
CADDY_VIEJO=0
if command -v caddy > /dev/null 2>&1 && [ -f /etc/caddy/Caddyfile ] \
    && grep -q "^# Gestionado por los instaladores nativos de JZTech" /etc/caddy/Caddyfile; then
  CADDY_VIEJO=1
fi

# 11. Resumen
ADMINS=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT count(*) FROM usuarios WHERE rol = 'admin'" 2>/dev/null || echo 0)
echo ""
echo "================================================================="
echo "INSTALACION COMPLETADA - JZTravell $VERSION"
echo "================================================================="
echo "Escuchando en: http://$DESTINO:$APP_PORT"
if [ -n "$DOMAIN" ]; then
  echo "Direccion publica: https://$DOMAIN (tiene que llegar a http://$DESTINO:$APP_PORT)"
fi
if [ "$ADMINS" = "0" ]; then
  echo ""
  echo "Token de configuracion inicial: $SETUP_TOKEN"
  echo "La pagina lo pide para crear el usuario administrador (sirve una sola vez)."
fi
echo ""
echo "Para actualizar mas adelante: sudo jztravell-actualizar"
if [ "$CADDY_VIEJO" = "1" ]; then
  echo ""
  echo "Aviso: sigue instalado el Caddy que configuraba una version anterior de este instalador (no se toco)."
  echo "Ocupa los puertos 80 y 443. Si ya no lo usa ninguna otra app, sacarlo con:"
  echo "  sudo systemctl disable --now caddy"
  echo "  sudo apt purge caddy"
fi
echo "================================================================="
