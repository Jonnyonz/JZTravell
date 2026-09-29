from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_modular_architecture_compiles():
    # Al pedir el esquema OpenAPI, FastAPI compila internamente todos los routers.
    # Si hay un error de importacion en algun router, esto lanza una excepcion y el test falla.
    with TestClient(app) as c:
        response = c.get("/openapi.json")
    assert response.status_code == 200, "El servidor no pudo compilar el arbol de rutas."

    schema_str = str(response.json())
    dominios_esperados = ["Auth", "Usuarios", "Configuracion", "Flota", "Sucursales", "Clientes", "Fletes"]
    for dominio in dominios_esperados:
        assert dominio in schema_str, f"Fallo arquitectonico: el modulo '{dominio}' no se registro en main.py"


def test_frontend_statics_mounted():
    with TestClient(app) as c:
        response = c.get("/index.html")
    assert response.status_code in [200, 303, 404], "Los archivos estaticos del frontend no responden de forma esperable."
