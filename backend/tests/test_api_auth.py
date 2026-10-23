"""Control de acceso de la API.

El panel borra hosts de Nagios y edita el inventario, así que lo que se
prueba acá es que nada de eso quede accesible sin sesión.
"""

import pytest
from fastapi.testclient import TestClient

from app import api
from app.auth import SESSION_COOKIE

client = TestClient(api.app, follow_redirects=False)


class _FakeConn:
    def close(self):
        pass


@pytest.fixture
def sin_sesion(monkeypatch):
    monkeypatch.setattr(api, "get_connection", lambda path: _FakeConn())
    monkeypatch.setattr(api, "get_session_user", lambda conn, token: None)


@pytest.fixture
def con_sesion(monkeypatch):
    monkeypatch.setattr(api, "get_connection", lambda path: _FakeConn())
    monkeypatch.setattr(api, "get_session_user", lambda conn, token: "enzo")


@pytest.mark.parametrize("ruta", [
    "/api/hosts",
    "/api/meta",
    "/api/insights",
    "/api/tags",
    "/api/replacements",
    "/api/inventory/pending",
    "/api/alarmas",
    "/api/estados",
    "/api/hosts/HOST1/eventos",
])
def test_api_sin_sesion_devuelve_401(sin_sesion, ruta):
    assert client.get(ruta).status_code == 401


@pytest.mark.parametrize("ruta,payload", [
    ("/api/nagios-uploader/execute-remove", {"host_name": "HOST1"}),
    ("/api/nagios-uploader/execute-add", {"rak": "RAK123"}),
    ("/api/nagios-uploader/register", {"rak": "RAK123"}),
    ("/api/hosts/HOST1/replace", {"nombre_pc": "X", "rak": "Y", "anydesk_id": "1"}),
    ("/api/poll", None),
])
def test_acciones_destructivas_bloqueadas_sin_sesion(sin_sesion, ruta, payload):
    """Lo que escribe en Nagios o en el inventario no puede quedar abierto."""
    resp = client.post(ruta, json=payload) if payload else client.post(ruta)
    assert resp.status_code == 401


def test_borrar_del_inventario_bloqueado_sin_sesion(sin_sesion):
    """Borra una fila del CSV de inventario de forma irreversible."""
    assert client.delete("/api/inventory/RAK123").status_code == 401


def test_cambiar_estado_operativo_bloqueado_sin_sesion(sin_sesion):
    """Cambiar un equipo a Baja lo saca del monitoreo: no puede quedar abierto."""
    resp = client.patch("/api/inventory/RAK123/estado", json={"estado": "Baja"})
    assert resp.status_code == 401


def test_pantalla_sin_sesion_redirige_al_login(sin_sesion):
    resp = client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_rutas_publicas_no_piden_sesion(sin_sesion):
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200


def test_estaticos_accesibles_para_dibujar_el_login(sin_sesion):
    """El CSS tiene que cargar en la pantalla de login, antes de haber sesión."""
    assert client.get("/static/css/styles.css").status_code == 200


def test_con_sesion_se_accede_normalmente(con_sesion, monkeypatch):
    monkeypatch.setattr(api, "get_tags", lambda: [])
    assert client.get("/api/tags").status_code == 200


def test_login_exitoso_setea_cookie(monkeypatch):
    monkeypatch.setattr(api, "get_connection", lambda path: _FakeConn())
    monkeypatch.setattr(api, "authenticate", lambda conn, u, p: True)
    monkeypatch.setattr(api, "create_session", lambda conn, u: "token-de-prueba")

    resp = client.post("/api/login", json={"username": "Enzo", "password": "clave"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "enzo"
    assert SESSION_COOKIE in resp.cookies


def test_login_con_credenciales_malas(monkeypatch):
    monkeypatch.setattr(api, "get_connection", lambda path: _FakeConn())
    monkeypatch.setattr(api, "authenticate", lambda conn, u, p: False)

    resp = client.post("/api/login", json={"username": "enzo", "password": "incorrecta"})
    assert resp.status_code == 401
    assert SESSION_COOKIE not in resp.cookies


def test_logout_borra_la_sesion(con_sesion, monkeypatch):
    borradas = []
    monkeypatch.setattr(api, "delete_session", lambda conn, token: borradas.append(token))

    resp = client.post("/api/logout")
    assert resp.status_code == 200
    assert len(borradas) == 1


def test_me_devuelve_el_usuario(con_sesion):
    assert client.get("/api/me").json()["username"] == "enzo"


# --- Cambio de contraseña por el propio usuario ---

def test_cambiar_contrasena_exitoso(con_sesion, monkeypatch):
    cambios = []
    monkeypatch.setattr(api, "authenticate", lambda conn, u, p: True)
    monkeypatch.setattr(api, "set_password", lambda conn, u, p: cambios.append((u, p)))
    monkeypatch.setattr(api, "create_session", lambda conn, u: "token-nuevo")

    resp = client.post("/api/change-password",
                       json={"current_password": "vieja123", "new_password": "nueva-segura"})
    assert resp.status_code == 200
    assert cambios == [("enzo", "nueva-segura")]
    # Se emite una sesión nueva para no echar a la persona de su propia pantalla
    assert SESSION_COOKIE in resp.cookies


def test_cambiar_contrasena_con_la_actual_incorrecta(con_sesion, monkeypatch):
    """Tener la sesión abierta no alcanza: hay que saber la contraseña vigente.

    Responde 422 y no 401 porque la sesión sigue siendo válida: con 401 el
    frontend echaría a la persona del panel por un error de tipeo.
    """
    monkeypatch.setattr(api, "authenticate", lambda conn, u, p: False)
    monkeypatch.setattr(api, "set_password", lambda conn, u, p: pytest.fail("no debería cambiarla"))

    resp = client.post("/api/change-password",
                       json={"current_password": "equivocada", "new_password": "nueva-segura"})
    assert resp.status_code == 422


def test_cambiar_contrasena_rechaza_las_cortas(con_sesion, monkeypatch):
    monkeypatch.setattr(api, "authenticate", lambda conn, u, p: True)
    monkeypatch.setattr(api, "set_password", lambda conn, u, p: pytest.fail("no debería cambiarla"))

    resp = client.post("/api/change-password",
                       json={"current_password": "vieja123", "new_password": "corta"})
    assert resp.status_code == 422


def test_cambiar_contrasena_rechaza_la_misma(con_sesion, monkeypatch):
    monkeypatch.setattr(api, "authenticate", lambda conn, u, p: True)
    monkeypatch.setattr(api, "set_password", lambda conn, u, p: pytest.fail("no debería cambiarla"))

    resp = client.post("/api/change-password",
                       json={"current_password": "12345678", "new_password": "12345678"})
    assert resp.status_code == 422


def test_cambiar_contrasena_sin_sesion(sin_sesion):
    resp = client.post("/api/change-password",
                       json={"current_password": "x", "new_password": "nueva-segura"})
    assert resp.status_code == 401


# --- Frescura de los estáticos ---

def test_los_estaticos_piden_revalidar(sin_sesion):
    """Si el navegador se queda con un main.js viejo contra un index.html nuevo,
    el JS busca elementos que ya no existen, falla a mitad del render y deja la
    pantalla vacía. No da error visible, así que hay que evitarlo de entrada."""
    resp = client.get("/static/js/main.js")
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_el_html_pide_revalidar(sin_sesion):
    """El HTML decide qué scripts pedir: cacheado, sigue pidiendo los viejos."""
    assert "no-cache" in client.get("/login").headers.get("cache-control", "")
