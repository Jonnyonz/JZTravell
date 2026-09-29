"""Test de regresion para get_client_ip tras migrar a jztech_core.net.real_ip
(ver seccion 3.2/6 de JZTech_Estado_y_Hoja_de_Ruta.md). No requiere base de
datos: get_client_ip no la toca."""

from starlette.requests import Request

from main import get_client_ip


def _make_request(client_ip: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "headers": headers,
        "path": "/",
        "query_string": b"",
        "client": (client_ip, 12345),
    }
    return Request(scope)


def test_ignores_forwarded_for_from_untrusted_origin():
    assert get_client_ip(_make_request("203.0.113.9", "10.0.0.1")) == "203.0.113.9"


def test_trusts_forwarded_for_from_default_trusted_proxy():
    # 127.0.0.1 esta en el TRUSTED_PROXIES por defecto de main.py.
    assert get_client_ip(_make_request("127.0.0.1", "203.0.113.9")) == "203.0.113.9"


def test_walks_multiple_trusted_hops():
    # 127.0.0.1 y ::1 son confiables por defecto; el primer salto no confiable
    # caminando desde la derecha es el cliente real.
    req = _make_request("127.0.0.1", "203.0.113.9, 198.51.100.1, ::1")
    assert get_client_ip(req) == "198.51.100.1"
