"""Motivo de reemplazo.

Sirve para contar por qué se cambian los equipos, así que lo que se prueba acá
es que el dato no se pueda omitir ni ensuciar: sin motivo no hay reemplazo, y
un valor fuera del catálogo se rechaza antes de tocar nada.
"""

import pytest
from fastapi.testclient import TestClient

from app import api
from app.auth import SESSION_COOKIE
from app.dashboard_service import (
    MOTIVOS_REEMPLAZO,
    MOTIVOS_VALIDOS,
    ReplaceResult,
    etiqueta_motivo,
)
from app.db import get_connection, get_all_replacements, record_host_replacement

client = TestClient(api.app, follow_redirects=False)


class _FakeConn:
    def close(self):
        pass


@pytest.fixture
def con_sesion(monkeypatch):
    monkeypatch.setattr(api, "get_connection", lambda path: _FakeConn())
    monkeypatch.setattr(api, "get_session_user", lambda conn, token: "enzo")


def _payload(**extra):
    base = {"nombre_pc": "Monroe 1231", "rak": "RAKNUEVO1234567890", "anydesk_id": "999"}
    base.update(extra)
    return base


# --- Catálogo ---

def test_el_catalogo_cubre_los_casos_que_se_usan():
    """Los tres que motivaron la función: refurbish, falla de PC y cambio de NUC."""
    assert {"refurbish", "falla_pc", "cambio_nuc"} <= MOTIVOS_VALIDOS


def test_cada_motivo_tiene_nombre_legible():
    for clave, etiqueta in MOTIVOS_REEMPLAZO:
        assert etiqueta and etiqueta != clave


def test_la_etiqueta_traduce_la_clave():
    assert etiqueta_motivo("falla_pc") == "Mal funcionamiento de la PC"


def test_un_motivo_viejo_o_desconocido_se_muestra_tal_cual():
    """La lista puede cambiar; lo ya registrado no se rompe ni se oculta."""
    assert etiqueta_motivo("motivo_que_ya_no_existe") == "motivo_que_ya_no_existe"


def test_sin_motivo_no_hay_etiqueta():
    assert etiqueta_motivo(None) is None
    assert etiqueta_motivo("") is None


def test_el_endpoint_devuelve_el_catalogo(con_sesion):
    cuerpo = client.get("/api/motivos").json()
    assert len(cuerpo) == len(MOTIVOS_REEMPLAZO)
    assert {m["valor"] for m in cuerpo} == MOTIVOS_VALIDOS


# --- El motivo es obligatorio ---

def test_reemplazar_sin_motivo_se_rechaza(con_sesion, monkeypatch):
    monkeypatch.setattr(api, "replace_host_inventory",
                        lambda *a, **k: pytest.fail("no debería llegar a reemplazar"))

    resp = client.post("/api/hosts/HOST1/replace", json=_payload())

    assert resp.status_code == 422
    assert "motivo" in resp.json()["detail"].lower()


def test_reemplazar_con_motivo_vacio_se_rechaza(con_sesion, monkeypatch):
    monkeypatch.setattr(api, "replace_host_inventory",
                        lambda *a, **k: pytest.fail("no debería llegar a reemplazar"))
    assert client.post("/api/hosts/HOST1/replace", json=_payload(motivo="")).status_code == 422


def test_un_motivo_fuera_del_catalogo_se_rechaza(con_sesion, monkeypatch):
    """Si no se valida, la tabla se llena de valores sueltos y deja de contar."""
    monkeypatch.setattr(api, "replace_host_inventory",
                        lambda *a, **k: pytest.fail("no debería llegar a reemplazar"))

    resp = client.post("/api/hosts/HOST1/replace", json=_payload(motivo="porque si"))

    assert resp.status_code == 422
    assert "desconocido" in resp.json()["detail"].lower()


def test_el_motivo_llega_a_la_capa_de_servicio(con_sesion, monkeypatch):
    recibidos = {}

    def fake_replace(host_name, **kwargs):
        recibidos.update(kwargs)
        return ReplaceResult(
            old_rak="VIEJO", old_host_name="VIEJO", new_host_name="NUEVO",
            nagios_applied=True, old_removed=True, validation_output="ok",
        )

    monkeypatch.setattr(api, "replace_host_inventory", fake_replace)

    resp = client.post("/api/hosts/HOST1/replace", json=_payload(motivo="cambio_nuc"))

    assert resp.status_code == 200
    assert recibidos["motivo"] == "cambio_nuc"


# --- Persistencia ---

def test_el_motivo_queda_guardado(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    record_host_replacement(
        conn, old_rak="VIEJO", new_rak="NUEVO", old_host_name="VIEJO",
        new_host_name="NUEVO", nombre_pc="Monroe 1231", ambiente="GCBA",
        replaced_at=1000, motivo="falla_disco",
    )
    assert get_all_replacements(conn)[0].motivo == "falla_disco"


def test_un_reemplazo_viejo_sin_motivo_sigue_leyendose(tmp_path):
    """Los registros anteriores a esta función quedan con motivo en NULL."""
    conn = get_connection(tmp_path / "t.db")
    record_host_replacement(
        conn, old_rak="VIEJO", new_rak="NUEVO", old_host_name="VIEJO",
        new_host_name="NUEVO", nombre_pc="Monroe 1231", ambiente="GCBA",
        replaced_at=1000,
    )
    assert get_all_replacements(conn)[0].motivo is None


def test_la_columna_se_agrega_a_una_base_que_no_la_tenia(tmp_path):
    """Migración: una base creada antes de la columna tiene que seguir abriendo."""
    import sqlite3
    ruta = tmp_path / "vieja.db"
    vieja = sqlite3.connect(ruta)
    vieja.execute("""CREATE TABLE host_replacements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, old_rak TEXT NOT NULL, new_rak TEXT NOT NULL,
        old_host_name TEXT, new_host_name TEXT NOT NULL, nombre_pc TEXT, ambiente TEXT,
        replaced_at INTEGER NOT NULL)""")
    vieja.execute("INSERT INTO host_replacements (old_rak, new_rak, new_host_name, replaced_at) "
                  "VALUES ('V', 'N', 'N', 1000)")
    vieja.commit(); vieja.close()

    conn = get_connection(ruta)  # dispara la migración
    columnas = [r[1] for r in conn.execute("PRAGMA table_info(host_replacements)")]

    assert "motivo" in columnas
    assert get_all_replacements(conn)[0].motivo is None
