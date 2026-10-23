"""Columna Estado del inventario y borrado de filas.

El Estado reemplaza al mecanismo de tags para decidir si un equipo está en
servicio. Lo que se prueba acá es que la lectura sea tolerante (la planilla
se edita a mano) y que el borrado no se lleve puesta la fila equivocada.
"""

import csv

import pytest

from app.inventory_loader import EstadoOperativo, load_inventory, parse_estado
from app.inventory_writer import (
    InventoryWriteError,
    delete_inventory_row,
    update_inventory_row,
)

CABECERA = ["Nombre PC", "RAK", "ID AnyDesk", "Cliente", "Estado"]


def escribir_csv(path, filas, cabecera=None):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cabecera or CABECERA)
        w.writerows(filas)
    return str(path)


# --- Lectura del Estado ---

@pytest.mark.parametrize("texto,esperado", [
    ("Produccion", EstadoOperativo.PRODUCCION),
    ("Producción", EstadoOperativo.PRODUCCION),
    ("PRODUCCION", EstadoOperativo.PRODUCCION),
    ("  producción  ", EstadoOperativo.PRODUCCION),
    ("En produccion", EstadoOperativo.PRODUCCION),
    ("Testing", EstadoOperativo.TESTING),
    ("TESTING", EstadoOperativo.TESTING),
    ("RMA", EstadoOperativo.RMA),
    ("Refurbish", EstadoOperativo.REFURBISH),
    ("Baja", EstadoOperativo.BAJA),
])
def test_el_estado_se_lee_aunque_este_escrito_distinto(texto, esperado):
    """La planilla se edita a mano: hay que aguantar mayúsculas y acentos."""
    assert parse_estado(texto) == esperado


@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_sin_estado_se_asume_produccion(vacio):
    """Las 408 filas que ya existían son equipos en servicio; no hay que
    obligar a completarlas una por una."""
    assert parse_estado(vacio) == EstadoOperativo.PRODUCCION


def test_un_estado_irreconocible_no_rompe_la_carga():
    assert parse_estado("cualquier cosa") == EstadoOperativo.PRODUCCION


def test_solo_produccion_es_productivo():
    assert EstadoOperativo.PRODUCCION.es_productivo is True
    for otro in (EstadoOperativo.TESTING, EstadoOperativo.RMA,
                 EstadoOperativo.REFURBISH, EstadoOperativo.BAJA):
        assert otro.es_productivo is False


def test_el_inventario_sin_columna_estado_sigue_cargando(tmp_path):
    """Compatibilidad: un CSV viejo tiene que seguir funcionando."""
    ruta = escribir_csv(
        tmp_path / "inv.csv",
        [["Monroe 1231", "RAK1", "111", "GCBA"]],
        cabecera=["Nombre PC", "RAK", "ID AnyDesk", "Cliente"],
    )
    entradas = load_inventory(ruta)
    assert entradas[0].estado == EstadoOperativo.PRODUCCION


def test_se_lee_el_estado_de_cada_fila(tmp_path):
    ruta = escribir_csv(tmp_path / "inv.csv", [
        ["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"],
        ["NUC repuesto", "RAK2", "222", "STOCK", "RMA"],
    ])
    estados = {e.nombre_pc: e.estado for e in load_inventory(ruta)}
    assert estados["Monroe 1231"] == EstadoOperativo.PRODUCCION
    assert estados["NUC repuesto"] == EstadoOperativo.RMA


# --- Escritura del Estado ---

def test_se_puede_cambiar_el_estado_de_un_equipo(tmp_path):
    """Es el caso Cardenas 2207: salió del refurbish y vuelve a producción."""
    ruta = escribir_csv(tmp_path / "inv.csv", [["Cardenas 2207", "RAK1", "111", "GCBA", "Refurbish"]])
    update_inventory_row(ruta, 2, estado="Produccion")
    assert load_inventory(ruta)[0].estado == EstadoOperativo.PRODUCCION


def test_cambiar_el_estado_no_toca_los_otros_campos(tmp_path):
    ruta = escribir_csv(tmp_path / "inv.csv", [["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"]])
    update_inventory_row(ruta, 2, estado="RMA")
    entrada = load_inventory(ruta)[0]
    assert (entrada.nombre_pc, entrada.rak, entrada.anydesk_id) == ("Monroe 1231", "RAK1", "111")


def test_se_agrega_la_columna_estado_si_el_csv_no_la_tiene(tmp_path):
    ruta = escribir_csv(
        tmp_path / "inv.csv",
        [["Monroe 1231", "RAK1", "111", "GCBA"]],
        cabecera=["Nombre PC", "RAK", "ID AnyDesk", "Cliente"],
    )
    update_inventory_row(ruta, 2, estado="Baja")
    assert load_inventory(ruta)[0].estado == EstadoOperativo.BAJA


# --- Borrado de filas ---

def test_se_borra_la_fila_pedida(tmp_path):
    ruta = escribir_csv(tmp_path / "inv.csv", [
        ["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"],
        ["Viejo sin uso", "RAK2", "222", "STOCK", "Baja"],
        ["Cantilo 5101", "RAK3", "333", "GCBA", "Produccion"],
    ])
    borrado = delete_inventory_row(ruta, 3, expected_rak="RAK2")

    assert borrado == "Viejo sin uso"
    assert [e.nombre_pc for e in load_inventory(ruta)] == ["Monroe 1231", "Cantilo 5101"]


def test_no_borra_si_el_rak_no_coincide(tmp_path):
    """Los números de fila se corren al borrar: sin esta guarda, una pestaña
    con la vista vieja se lleva puesto al vecino."""
    ruta = escribir_csv(tmp_path / "inv.csv", [
        ["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"],
        ["Cantilo 5101", "RAK3", "333", "GCBA", "Produccion"],
    ])
    with pytest.raises(InventoryWriteError, match="cambió desde que cargaste"):
        delete_inventory_row(ruta, 2, expected_rak="RAK_QUE_NO_ESTA")

    assert len(load_inventory(ruta)) == 2


def test_el_rak_se_compara_normalizado(tmp_path):
    """El inventario trae prefijos y guiones inconsistentes."""
    ruta = escribir_csv(tmp_path / "inv.csv", [["Monroe 1231", "TGACSL-RAK1", "111", "GCBA", "Produccion"]])
    delete_inventory_row(ruta, 2, expected_rak="tgacsl rak1")
    assert load_inventory(ruta) == []


def test_borrar_una_fila_inexistente_avisa(tmp_path):
    ruta = escribir_csv(tmp_path / "inv.csv", [["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"]])
    with pytest.raises(InventoryWriteError, match="ya no existe"):
        delete_inventory_row(ruta, 99, expected_rak="RAK1")


def test_no_se_puede_borrar_el_encabezado(tmp_path):
    ruta = escribir_csv(tmp_path / "inv.csv", [["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"]])
    with pytest.raises(InventoryWriteError):
        delete_inventory_row(ruta, 1, expected_rak="RAK")


# --- Ubicar la fila cuando el RAK está en el nombre ---

def test_se_ubica_por_nombre_si_la_columna_rak_esta_vacia(tmp_path, monkeypatch):
    """Varias filas de STOCK traen el RAK cargado como nombre y la columna RAK
    vacía. Sin este respaldo quedan sin forma de cambiarles la situación."""
    from app import dashboard_service

    ruta = escribir_csv(tmp_path / "inv.csv", [
        ["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"],
        ["Y07D3S0Z09QKL9T44J6T", "", "222", "STOCK", "Testing"],
    ])
    monkeypatch.setattr(dashboard_service, "load_inventory_csv_path", lambda: ruta)
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: load_inventory(ruta))

    entry = dashboard_service._buscar_por_rak("Y07D3S0Z09QKL9T44J6T")
    assert entry.nombre_pc == "Y07D3S0Z09QKL9T44J6T"


def test_el_rak_tiene_prioridad_sobre_el_nombre(tmp_path, monkeypatch):
    """Si hay un RAK real que coincide, gana ese: el nombre es solo el respaldo."""
    from app import dashboard_service

    ruta = escribir_csv(tmp_path / "inv.csv", [
        ["OTRO", "COINCIDE", "111", "GCBA", "Produccion"],
        ["COINCIDE", "", "222", "STOCK", "Testing"],
    ])
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: load_inventory(ruta))

    assert dashboard_service._buscar_por_rak("COINCIDE").nombre_pc == "OTRO"


def test_un_identificador_vacio_se_rechaza(tmp_path, monkeypatch):
    from app import dashboard_service

    ruta = escribir_csv(tmp_path / "inv.csv", [["Monroe 1231", "RAK1", "111", "GCBA", "Produccion"]])
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: load_inventory(ruta))

    with pytest.raises(dashboard_service.InventoryRowNotFoundError):
        dashboard_service._buscar_por_rak("")
