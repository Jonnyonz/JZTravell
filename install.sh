#!/bin/bash
# ==============================================================================
# Instalador con Docker de JZTravell (la instalacion sin Docker es install-native.sh)
# ==============================================================================
#   ./install.sh                                        (en la carpeta del repo, o via curl ... | bash: clona en ./jztravell)
#   JZTRAVELL_DOMAIN=fletes.empresa.com ./install.sh    dominio publico: certificado automatico
#
# Instala y, si se vuelve a correr, ACTUALIZA: trae la ultima version (git pull --ff-only), respalda la base en
# backups/ antes de reconstruir y configura HTTPS con un contenedor de Caddy (perfil "https" del compose). Sin
# HTTPS la sesion no se guarda desde otra PC y el GPS del chofer no funciona.
#
# Variables opcionales: JZTRAVELL_DOMAIN, JZTRAVELL_IP (sin dominio: IP para el certificado local),
# JZTRAVELL_HTTPS=no, JZTRAVELL_HTTPS_PORT, JZTRAVELL_NO_UPDATE=1, JZTRAVELL_RESET_DB=1 (borrar la base de una
# instalacion anterior cuando no hay .env).
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
# Generado por install.sh (Docker). Ver .env.example. Lo de HTTPS (JZTRAVELL_*, CADDY_*, COMPOSE_PROFILES) lo
# mantiene install.sh.
PROJECT_NAME=JZ_Travel
PORT=8010
APP_BIND=127.0.0.1
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

# 4. HTTPS con Caddy (servicio "caddy" del compose, perfil https).
HTTPS="${JZTRAVELL_HTTPS:-$(leer JZTRAVELL_HTTPS)}"; HTTPS="${HTTPS:-si}"
NO_SE_PUDO=0
CADDY_CORRIENDO=0
if docker compose ps --status running --services 2>/dev/null | grep -qx caddy; then CADDY_CORRIENDO=1; fi
DOMAIN="${JZTRAVELL_DOMAIN:-$(leer JZTRAVELL_DOMAIN)}"
if [ -z "$DOMAIN" ] && [ "$NUEVA" = "1" ] && [ -r /dev/tty ]; then
    read -r -p "Dominio publico de JZTravell (ej. fletes.empresa.com; Enter para usar la IP del servidor): " DOMAIN < /dev/tty || DOMAIN=""
fi
if [ "$HTTPS" = "si" ]; then
    IP="${JZTRAVELL_IP:-$(leer JZTRAVELL_IP)}"
    if [ -z "$DOMAIN" ] && [ -z "$IP" ]; then
        IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        if [ -z "$IP" ]; then IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"; fi
    fi
    if [ -z "$DOMAIN" ] && ! [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "AVISO: no se pudo saber la IP del servidor; no se configura HTTPS (indicarla con JZTRAVELL_IP=192.168.1.10)."
        HTTPS="no"; NO_SE_PUDO=1
    fi
fi
if [ "$HTTPS" = "si" ]; then
    # Un puerto esta "ocupado" si lo usa otro programa (el Caddy de esta instalacion no cuenta).
    ocupado() { [ "$CADDY_CORRIENDO" = "0" ] && command -v ss > /dev/null && [ -n "$(ss -ltnH "( sport = :$1 )" 2>/dev/null)" ]; }
    PUERTO_HTTPS="${JZTRAVELL_HTTPS_PORT:-$(leer CADDY_HTTPS_PORT)}"
    if [ -z "$PUERTO_HTTPS" ]; then
        for P in 443 8443 9443 10443; do
            PUERTO_HTTPS="$P"
            if ! ocupado "$P"; then break; fi
        done
    fi
    if ocupado "$PUERTO_HTTPS"; then
        echo "AVISO: el puerto $PUERTO_HTTPS ya esta en uso; no se configura HTTPS (elegir otro con JZTRAVELL_HTTPS_PORT=...)."
        HTTPS="no"; NO_SE_PUDO=1
    fi
fi
if [ "$HTTPS" = "si" ]; then
    if grep -qE '^CADDY_HTTP_PORT=' .env; then
        PUERTO_HTTP="$(leer CADDY_HTTP_PORT)"; BIND_HTTP="$(leer CADDY_HTTP_BIND)"
    elif [ "$PUERTO_HTTPS" = "443" ] && ! ocupado 80; then
        PUERTO_HTTP=80; BIND_HTTP=0.0.0.0
    else
        PUERTO_HTTP=""; BIND_HTTP=127.0.0.1
    fi
    if [ -n "$DOMAIN" ]; then HOST="$DOMAIN"; else HOST="$IP"; fi
    SITIO="https://$HOST"
    if [ "$PUERTO_HTTPS" != "443" ]; then SITIO="$SITIO:$PUERTO_HTTPS"; fi
    mkdir -p caddy
    {
        # default_sni: por IP el navegador no manda el nombre del sitio (SNI) y Caddy, dentro del contenedor, no
        # ve la IP del servidor: sin esto no sabe que certificado dar y corta la conexion.
        GLOBALES=""
        if [ "$PUERTO_HTTP" != "80" ] || [ "$PUERTO_HTTPS" != "443" ]; then GLOBALES="${GLOBALES}	auto_https disable_redirects
"; fi
        if [ -z "$DOMAIN" ]; then GLOBALES="${GLOBALES}	default_sni $IP
"; fi
        if [ -n "$GLOBALES" ]; then printf '{\n%s}\n\n' "$GLOBALES"; fi
        echo "# Generado por install.sh: se vuelve a escribir en cada corrida (no editar a mano)."
        if [ -n "$DOMAIN" ]; then
            printf '%s {\n\treverse_proxy backend:8000\n}\n' "$DOMAIN"
        else
            printf 'https://%s, https://localhost {\n\ttls internal\n\treverse_proxy backend:8000\n}\n' "$IP"
        fi
    } > caddy/Caddyfile
    poner JZTRAVELL_HTTPS si
    poner JZTRAVELL_DOMAIN "$DOMAIN"
    poner JZTRAVELL_IP "$IP"
    poner COMPOSE_PROFILES https
    poner CADDY_HTTPS_PORT "$PUERTO_HTTPS"
    poner CADDY_HTTP_PORT "$PUERTO_HTTP"
    poner CADDY_HTTP_BIND "$BIND_HTTP"
else
    # "no" queda guardado solo si lo eligio el usuario; si no se pudo (puertos o IP), se reintenta la proxima vez.
    if [ "$NO_SE_PUDO" != "1" ]; then poner JZTRAVELL_HTTPS no; fi
    poner COMPOSE_PROFILES ""
    if [ "$CADDY_CORRIENDO" = "1" ]; then docker compose --profile https stop caddy > /dev/null 2>&1 || true; fi
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

# 6. Construir y levantar.
echo "Desplegando con Docker Compose (la primera vez tarda por la construccion de las imagenes)..."
docker compose up -d --build

PUERTO_APP="$(leer PORT)"; PUERTO_APP="${PUERTO_APP:-8000}"
HTTPS_OK=0
if [ "$HTTPS" = "si" ] && command -v curl > /dev/null; then
    for _ in $(seq 1 45); do
        if curl -fsSk --max-time 5 --resolve "$HOST:$PUERTO_HTTPS:127.0.0.1" "https://$HOST:$PUERTO_HTTPS/api/setup/status" > /dev/null 2>&1; then
            HTTPS_OK=1; break
        fi
        sleep 2
    done
    if [ -z "$DOMAIN" ]; then
        docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy/ca-local.crt > /dev/null 2>&1 || true
    fi
fi

echo ""
echo "=================================================="
echo "JZTravell se instalo e inicio correctamente"
echo "=================================================="
if [ "$HTTPS" = "si" ]; then
    echo "Entrar desde el navegador: $SITIO"
else
    echo "Entrar desde el navegador: http://localhost:${PUERTO_APP} (en este servidor)"
fi
if [ -n "$SETUP_TOKEN" ]; then
    echo ""
    echo "Token de configuracion inicial: $SETUP_TOKEN"
    echo "La pagina lo pide para crear el usuario administrador (sirve una sola vez)."
fi
echo "Para actualizar mas adelante: volver a correr ./install.sh en esta carpeta."
echo ""
echo "------------------------------ AVISO HTTPS ------------------------------"
if [ "$HTTPS" = "si" ]; then
    echo "Se configuro HTTPS con Caddy (contenedor jztravel_caddy): $SITIO"
    if [ "$HTTPS_OK" != "1" ]; then
        echo "ATENCION: el HTTPS todavia no responde. Ver: docker compose logs caddy"
    fi
    if [ -n "$DOMAIN" ]; then
        echo "- El certificado lo saca Caddy solo: $DOMAIN tiene que apuntar a este servidor y los puertos 80 y"
        echo "  443 tienen que llegar desde internet."
    else
        echo "- Sin dominio, el certificado es de la CA local de Caddy: el navegador avisa que la conexion no es"
        echo "  privada hasta que se instala en cada PC y celular el certificado raiz:"
        echo "  $PWD/caddy/ca-local.crt (o aceptar la excepcion del navegador para probar)."
        echo "- Para usar un dominio: JZTRAVELL_DOMAIN=fletes.empresa.com ./install.sh"
    fi
    if [ "$PUERTO_HTTPS" != "443" ]; then
        echo "- El puerto 443 lo usa otro programa de este servidor: HTTPS quedo en el $PUERTO_HTTPS."
    fi
    echo "- Para no usar HTTPS: JZTRAVELL_HTTPS=no ./install.sh"
else
    echo "HTTPS desactivado: se entra por http en el puerto ${PUERTO_APP}."
    echo "Desde otra PC no se puede iniciar sesion por http (cookie Secure) y el GPS del chofer no funciona."
    echo "Para activarlo: JZTRAVELL_HTTPS=si ./install.sh"
fi
echo "=================================================="
