# Notas de los parches

Cambios de JZTravell, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 2026-10-05

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
