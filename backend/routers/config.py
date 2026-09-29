from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
import asyncpg

from database import get_db, require_role

router = APIRouter(prefix="/api", tags=["Configuracion"])


class ConfigUpdate(BaseModel):
    nombre_empresa: str
    gps_polling_sec: int
    google_oauth_enabled: bool
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_url: Optional[str] = None
    ciudad_defecto: str
    provincia_defecto: str
    pais_defecto: str
    manejar_volumenes: bool


@router.get("/config")
async def get_config(db: asyncpg.Connection = Depends(get_db)):
    return dict(await db.fetchrow("SELECT nombre_empresa, gps_polling_sec, google_oauth_enabled, google_client_id, google_redirect_url, ciudad_defecto, provincia_defecto, pais_defecto, manejar_volumenes FROM configuracion_sistema WHERE id=1"))


@router.post("/config")
async def update_config(data: ConfigUpdate, db: asyncpg.Connection = Depends(get_db), user=Depends(require_role(['admin']))):
    await db.execute("""
        UPDATE configuracion_sistema SET nombre_empresa=$1, gps_polling_sec=$2, google_oauth_enabled=$3, google_client_id=$4, google_client_secret=$5, google_redirect_url=$6,
        ciudad_defecto=$7, provincia_defecto=$8, pais_defecto=$9, manejar_volumenes=$10 WHERE id=1
    """, data.nombre_empresa, data.gps_polling_sec, data.google_oauth_enabled, data.google_client_id, data.google_client_secret, data.google_redirect_url, data.ciudad_defecto, data.provincia_defecto, data.pais_defecto, data.manejar_volumenes)
    return {"status": "success"}
