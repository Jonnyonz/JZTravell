# Se construye desde la raiz del repo (ver docker-compose.yml: context "."). El codigo va dentro de la
# imagen (no se monta): actualizar = reconstruir la imagen.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

RUN useradd --system --uid 10001 --no-create-home jztravell
USER jztravell

# La app sirve el frontend desde ../frontend y lee las migraciones de backend/migrations.
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/setup/status', timeout=4)"

# --no-proxy-headers: la IP real la resuelve la app segun TRUSTED_PROXIES (jztech_core.net).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
