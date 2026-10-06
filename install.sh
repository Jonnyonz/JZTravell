#!/bin/bash
# ==============================================================================
# Instalador con Docker de JZTravell (la instalacion sin Docker es install-native.sh)
# ==============================================================================
#   ./install.sh                                        (en la carpeta del repo, o via curl ... | bash: clona en ./jztravell)
#   JZTRAVELL_DOMAIN=fletes.empresa.com ./install.sh    dominio publico con el que se va a entrar
#
# Instala y, si se vuelve a correr, ACTUALIZA: trae la ultima version (git pull --ff-only), respalda la base en
# backups/ antes de reconstruir y deja la app escuchando por http en APP_BIND:PORT (por defecto 0.0.0.0:8010).
# No levanta ningun proxy (sin Caddy): el HTTPS lo pone el proxy del servidor, que reenvia el dominio a ese puerto.
# Una instalacion anterior con Caddy propio se pasa sola a este esquema.
#
# Variables opcionales: JZTRAVELL_DOMAIN, JZTRAVELL_BIND (interfaz, por defecto 0.0.0.0), JZTRAVELL_PORT,
# JZTRAVELL_PROXY_IP (IP del proxy si esta en otro equipo: se suma a TRUSTED_PROXIES), JZTRAVELL_NO_UPDATE=1,
# JZTRAVELL_RESET_DB=1 (borrar la base de una instalacion anterior cuando no hay .env).
# ==============================================================================
set -e

echo "=================================================="
echo "  Instalador de JZTravell TMS (Docker)            "
echo "=================================================="

for cmd in docker git openssl; do
    if ! command -v "$cmd" > /dev/null; then
        echo "ERROR: falta $cmd. Instalarlo antes de continuar (Docker: https://docs.docker.com/engine/install/)."
        exit 1
    fi
done
if ! docker compose version > /dev/null 2>&1; then
    echo "ERROR: falta el plugin 'docker compose'."
    exit 1
fi

# 1. Codigo: si no estamos en el repo, se clona (o se usa ./jztravell de una instalacion anterior).
REPO_URL="${JZTRAVELL_REPO_URL:-https://github.com/Jonnyonz/JZTravell.git}"
if [ ! -f "docker-compose.yml" ] || [ ! -f "init_db.sql" ]; then
    if [ -f "jztravell/docker-compose.yml" ]; then
        cd jztravell
    else
        echo "Descargando el codigo desde ${REPO_URL}..."
        git clone "${REPO_URL}" jztravell
        cd jztravell
    fi
fi

# 2. Actualizar: solo avanza (--ff-only); con cambios locales o sin conexion se detiene sin tocar nada.
if [ -d .git ] && [ "${JZTRAVELL_NO_UPDATE:-}" != "1" ]; then
    GIT="git -c safe.directory=$PWD"
    ANTES=$($GIT rev-parse --short HEAD)
    echo "Buscando actualizaciones..."
    if ! $GIT pull --ff-only --quiet; then
        echo "ERROR: no se pudo traer la version nueva (cambios locales en $PWD o sin conexion)."
        echo "No se toco nada: la version instalada sigue funcionando ($ANTES)."
        exit 1
    fi
    DESPUES=$($GIT rev-parse --short HEAD)
    if [ "$ANTES" = "$DESPUES" ]; then
        echo "Ya esta en la ultima version ($DESPUES)."
    else
        echo "Codigo actualizado: $ANTES -> $DESPUES (los cambios estan en CHANGELOG.md)."
        # bash sigue leyendo el install.sh que arranco: se relanza el recien bajado.
        exec env JZTRAVELL_NO_UPDATE=1 bash ./install.sh "$@"
    fi
fi

leer() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'; }
poner() { if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }

# 3. .env: se genera solo la primera vez (las claves no se pisan nunca).
SETUP_TOKEN=""
NUEVA=0
if [ ! -f .env ]; then
    NUEVA=1
    # Postgres solo aplica la clave la primera vez que inicializa sus datos (./postgres-data): un .env nuevo no
    # sirve contra la base de una instalacion anterior. Si esa carpeta tiene datos, no se toca nada salvo que
    # se pida explicitamente.
    if [ -d "./postgres-data" ] && [ -n "$(ls -A ./postgres-data 2>/dev/null)" ]; then
        if [ "${JZTRAVELL_RESET_DB:-}" = "1" ]; then
            echo "JZTRAVELL_RESET_DB=1: se BORRA la base de la instalacion anterior (./postgres-data)."
            docker compose down > /dev/null 2>&1 || true
            rm -rf "./postgres-data"
        else
            echo "ERROR: hay una base de una instalacion anterior (./postgres-data) pero no hay .env."
            echo "No se borro nada. Opciones:"
            echo "  - Restaurar el .env de esa instalacion en $PWD y volver a correr ./install.sh"
            echo "  - Empezar de cero BORRANDO esos datos: JZTRAVELL_RESET_DB=1 ./install.sh"
            exit 1
        fi
    fi
    SETUP_TOKEN=$(openssl rand -hex 24)
    cat > .env <<EOF
# Generado por install.sh (Docker). Ver .env.example. JZTRAVELL_DOMAIN, PORT, APP_BIND y TRUSTED_PROXIES los
# mantiene install.sh (se cambian con JZTRAVELL_DOMAIN, JZTRAVELL_PORT, JZTRAVELL_BIND y JZTRAVELL_PROXY_IP).
PROJECT_NAME=JZ_Travel
PORT=8010
APP_BIND=0.0.0.0
POSTGRES_USER=jzadmin
POSTGRES_PASSWORD=$(openssl rand -hex 24)
POSTGRES_DB=jzflete_db
SETUP_TOKEN=${SETUP_TOKEN}
TRUSTED_PROXIES=127.0.0.1/32,::1/128,172.16.0.0/12
EOF
    chmod 600 .env
    echo "Archivo .env generado con claves aleatorias."
else
    echo "Se encontro un .env: se conserva la configuracion."
fi

# 4. Red. El HTTPS lo pone el proxy del servidor: la app solo escucha en APP_BIND:PORT para que el proxy llegue.
TRUSTED_DEFAULT="127.0.0.1/32,::1/128,172.16.0.0/12"   # el mismo default que la app (backend/database.py)
for VIEJA in JZTRAVELL_HTTPS JZTRAVELL_HTTPS_PORT JZTRAVELL_IP; do
    if [ -n "${!VIEJA:-}" ]; then echo "AVISO: $VIEJA ya no se usa (ya no hay Caddy); se ignora."; fi
done
# Una instalacion anterior con Caddy (perfil https del compose) deja en el .env claves que ya no se usan.
CON_CADDY=0
if grep -qE '^(CADDY_[A-Z_]+|JZTRAVELL_HTTPS|JZTRAVELL_IP)=' .env || [ "$(leer COMPOSE_PROFILES)" = "https" ]; then
    CON_CADDY=1
    echo "Instalacion anterior con Caddy: se saca Caddy y sus claves del .env."
    if [ "$(leer COMPOSE_PROFILES)" = "https" ]; then sed -i '/^COMPOSE_PROFILES=/d' .env; fi
    sed -i -E '/^(CADDY_[A-Z_]+|JZTRAVELL_HTTPS|JZTRAVELL_IP)=/d' .env
    sed -i -e '/^# mantiene install\.sh\.$/d' -e 's/^# Generado por install\.sh (Docker)\..*/# Generado por install.sh (Docker). Ver .env.example./' .env
fi

DOMAIN="${JZTRAVELL_DOMAIN:-$(leer JZTRAVELL_DOMAIN)}"
if [ -z "$DOMAIN" ] && [ "$NUEVA" = "1" ] && [ -r /dev/tty ]; then
    read -r -p "Dominio con el que se va a entrar a JZTravell (ej. fletes.empresa.com; Enter para ninguno): " DOMAIN < /dev/tty || DOMAIN=""
fi
DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%/}"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
    echo "ERROR: dominio invalido: $DOMAIN"
    exit 1
fi
poner JZTRAVELL_DOMAIN "$DOMAIN"

# Interfaz: la pedida o la del .env. Una instalacion con Caddy escuchaba solo en 127.0.0.1 (el proxy no llegaria).
BIND="${JZTRAVELL_BIND:-$(leer APP_BIND)}"
if [ -z "$BIND" ] || { [ -z "${JZTRAVELL_BIND:-}" ] && [ "$CON_CADDY" = "1" ] && [ "$BIND" = "127.0.0.1" ]; }; then
    BIND="0.0.0.0"
fi
poner APP_BIND "$BIND"
PUERTO_APP="${JZTRAVELL_PORT:-$(leer PORT)}"; PUERTO_APP="${PUERTO_APP:-8000}"
if ! [[ "$PUERTO_APP" =~ ^[0-9]+$ ]]; then
    echo "ERROR: puerto invalido: $PUERTO_APP"
    exit 1
fi
poner PORT "$PUERTO_APP"

# Proxy en otro equipo: su IP tiene que estar en TRUSTED_PROXIES para tomar la IP real del cliente (X-Forwarded-For).
PROXY_IP="${JZTRAVELL_PROXY_IP:-}"
if [ -n "$PROXY_IP" ]; then
    if ! [[ "$PROXY_IP" =~ ^[0-9A-Fa-f.:]+(/[0-9]+)?$ ]]; then
        echo "ERROR: JZTRAVELL_PROXY_IP invalida: $PROXY_IP"
        exit 1
    fi
    PROXIES="$(leer TRUSTED_PROXIES)"; PROXIES="${PROXIES:-$TRUSTED_DEFAULT}"
    if ! tr ',' '\n' <<< "$PROXIES" | tr -d ' ' | grep -qxF "$PROXY_IP"; then
        poner TRUSTED_PROXIES "$PROXIES,$PROXY_IP"
        echo "Se agrego $PROXY_IP a TRUSTED_PROXIES."
    fi
fi

# 5. Copia de la base antes de reconstruir.
if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
    mkdir -p backups
    COPIA="backups/antes_de_actualizar_$(date +%Y-%m-%d_%H%M%S).dump"
    # < /dev/null: con "curl ... | bash" el script llega por stdin y exec se comeria el resto.
    if docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' < /dev/null > "$COPIA"; then
        chmod 600 "$COPIA"
        echo "Copia de la base de datos: $PWD/$COPIA"
    else
        rm -f "$COPIA"
        echo "ERROR: no se pudo copiar la base de datos; no se actualizo nada."
        exit 1
    fi
fi

# 6. Construir y levantar (--remove-orphans saca el contenedor de Caddy de una version anterior).
SE_SACO_CADDY=0
if docker inspect jztravel_caddy > /dev/null 2>&1; then SE_SACO_CADDY=1; fi
echo "Desplegando con Docker Compose (la primera vez tarda por la construccion de las imagenes)..."
docker compose up -d --build --remove-orphans
if docker inspect jztravel_caddy > /dev/null 2>&1; then docker rm -f jztravel_caddy > /dev/null; fi
rm -rf caddy   # Caddyfile y certificado de la CA local que generaba la version anterior
PROYECTO="$(docker compose config 2>/dev/null | sed -n 's/^name: //p' | head -n1)"
VOLUMENES_CADDY=""
if [ -n "$PROYECTO" ]; then
    VOLUMENES_CADDY="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROYECTO" 2>/dev/null \
        | grep -E '_caddy_(data|config)$' | tr '\n' ' ' || true)"
fi

if [ "$BIND" = "0.0.0.0" ]; then LOCAL="127.0.0.1"; else LOCAL="$BIND"; fi
APP_OK=0
if command -v curl > /dev/null; then
    for _ in $(seq 1 45); do
        if curl -fsS --max-time 5 "http://$LOCAL:$PUERTO_APP/api/setup/status" > /dev/null 2>&1; then APP_OK=1; break; fi
        sleep 2
    done
fi

IP_SERVIDOR="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -z "$IP_SERVIDOR" ]; then
    IP_SERVIDOR="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
fi
IP_SERVIDOR="${IP_SERVIDOR:-<IP del servidor>}"
if [ "$BIND" = "0.0.0.0" ]; then DESTINO="$IP_SERVIDOR"; else DESTINO="$BIND"; fi

echo ""
echo "=================================================="
echo "JZTravell se instalo e inicio correctamente"
echo "=================================================="
echo "Escuchando en: http://$DESTINO:$PUERTO_APP"
if [ -n "$DOMAIN" ]; then
    echo "Direccion publica: https://$DOMAIN (tiene que llegar a http://$DESTINO:$PUERTO_APP)"
fi
if [ "$APP_OK" != "1" ]; then
    echo "ATENCION: la app todavia no responde en http://$LOCAL:$PUERTO_APP. Ver: docker compose logs backend"
fi
if [ -n "$SETUP_TOKEN" ]; then
    echo ""
    echo "Token de configuracion inicial: $SETUP_TOKEN"
    echo "La pagina lo pide para crear el usuario administrador (sirve una sola vez)."
fi
echo "Para actualizar mas adelante: volver a correr ./install.sh en esta carpeta."
if [ "$SE_SACO_CADDY" = "1" ]; then
    echo ""
    echo "Se saco el Caddy de la version anterior (contenedor jztravel_caddy): los puertos 80 y 443 quedaron libres."
fi
if [ -n "$VOLUMENES_CADDY" ]; then
    echo "Quedaron los volumenes del Caddy anterior (sus certificados). Para borrarlos: docker volume rm $VOLUMENES_CADDY"
fi
echo "=================================================="
