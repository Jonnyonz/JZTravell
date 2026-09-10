#!/bin/bash
set -e

echo "=================================================="
echo "  Instalador Automatico de JZTravell TMS           "
echo "=================================================="

# 1. Verificar dependencias del sistema
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker no esta instalado en este servidor."
    echo "Por favor, instala Docker antes de continuar."
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo "ERROR: Git no esta instalado."
    echo "Instalalo ejecutando: sudo apt update && sudo apt install git -y"
    exit 1
fi

# 2. Si no existen los archivos del proyecto, clonar el repositorio publico
if [ ! -f "docker-compose.yml" ]; then
    echo "Descargando codigo fuente desde GitHub..."
    git clone https://github.com/Jonnyonz/JZTravell.git jztravell
    cd jztravell
fi

# 3. Generar archivo .env si no existe
if [ ! -f .env ]; then
    echo "Configurando variables de entorno y claves de seguridad (.env)..."
    DB_PASS=$(openssl rand -hex 16 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 24)
    SETUP_TOKEN=$(openssl rand -hex 12 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 24)

    cat <<EOF > .env
PROJECT_NAME=JZ_Travel
PORT=8000

POSTGRES_USER=jzadmin
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=jzflete_db

OSRM_REGION_URL=http://download.geofabrik.de/south-america/argentina-latest.osm.pbf

SETUP_TOKEN=${SETUP_TOKEN}
EOF
    echo "Archivo .env generado con contrasenas seguras."
else
    echo "Se detecto un archivo .env existente. Manteniendo configuracion."
fi

# 4. Construir y levantar contenedores con Docker Compose
echo "Desplegando servicios con Docker Compose (esto puede tardar varios minutos la primera vez, por la descarga del mapa OSRM)..."
docker compose up -d --build

echo ""
echo "=================================================="
echo "JZTravell se instalo e inicio correctamente"
echo "=================================================="
echo "Puedes acceder desde tu navegador en:"
echo "http://localhost:8000 o http://$(hostname -I | awk '{print $1}'):8000"
if [ -n "$SETUP_TOKEN" ]; then
    echo ""
    echo "Token de configuracion inicial: $SETUP_TOKEN"
    echo "Entra a la URL de arriba: te va a pedir este token para crear el usuario administrador."
    echo "Se usa una sola vez (despues de crear el admin, deja de servir)."
fi
echo "=================================================="
