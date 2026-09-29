import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from database import init_db_schema, close_db_pool
from routers import auth, users, config, fleet, branches, clients, shipments


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_schema()
    yield
    await close_db_pool()


app = FastAPI(title="JZ Tech Solutions - API Logística", lifespan=lifespan)

# === REGISTRO DE ROUTERS MODULARES ===
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(config.router)
app.include_router(fleet.router)
app.include_router(branches.router)
app.include_router(clients.router)
app.include_router(shipments.router)


# --- MIDDLEWARE DE SEGURIDAD ---
@app.middleware("http")
async def security_and_csrf_middleware(request: Request, call_next):
    if request.method in ["POST", "PUT", "DELETE"]:
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token or csrf_token != request.cookies.get("csrf_token"):
            return JSONResponse(status_code=403, content={"detail": "Accion rechazada por validacion de seguridad CSRF."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if "csrf_token" not in request.cookies:
        response.set_cookie(key="csrf_token", value=secrets.token_urlsafe(32), httponly=False, secure=True, samesite='lax')
    return response


app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
