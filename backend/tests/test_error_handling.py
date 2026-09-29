"""Errores no manejados: el cliente recibe un mensaje generico, nunca el detalle (str(e)).
No requiere base de datos. No agrega rutas a la app (el test de deny-by-default las veria)."""

import asyncio
import json

from starlette.requests import Request

from main import app


def test_generic_error_handler_registered_and_hides_detail():
    handler = app.exception_handlers.get(Exception)
    assert handler is not None, "falta install_generic_error_handler en main.py"
    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""})
    resp = asyncio.run(handler(request, RuntimeError("detalle interno: password=super-secreta")))
    assert resp.status_code == 500
    # "detail": el formato que lee el frontend de JZTravell (el de FastAPI).
    assert json.loads(resp.body) == {"detail": "Error interno del servidor."}
    assert b"super-secreta" not in resp.body
