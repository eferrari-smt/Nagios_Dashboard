"""Alarmas por equipo y bandeja de intervención.

El criterio central que se prueba acá: un check en OK no es una alarma, y la
bandeja de intervención solo admite CRITICAL sostenida más de 48 h. Si eso se
afloja, la bandeja se llena de ruido crónico y se deja de mirar.
"""

import pytest

from app.alarmas import (
    TIPOS_ALARMA,
    UMBRAL_INTERVENCION_SEGUNDOS,
    detectar_agentes_mudos,
    etiqueta_de,
    get_alarmas,
    get_intervenciones,
    resumir_por_tipo,
)
from app.dashboard_service import HostView
from app.db import ServiceStatusRow
from app.inventory_loader import EstadoOperativo, InventoryEntry
from app.matching import MatchType
from app.pattern_classifier import Pattern
from app.status_parser import HostState, ServiceState
from app.tags_store import Tag

AHORA = 1_000_000
HORA = 3600


def make_service(description="CPU Load", state=ServiceState.CRITICAL, desde_horas=1, output="CRITICAL - CPU Load: 99%"):
    return ServiceStatusRow(
        host_name="HOST1",
        service_description=description,
        current_state=state,
        last_check=AHORA,
        last_state_change=AHORA - int(desde_horas * HORA),
        plugin_output=output,
        polled_at=AHORA,
    )


def make_host(host_name="HOST1", services=None, tags=None, nombre_pc="Av. Monroe 1231",
              ambiente="GCBA", estado=EstadoOperativo.PRODUCCION):
    entry = (
        InventoryEntry(nombre_pc=nombre_pc, rak="RAK123", anydesk_id="1", cliente=ambiente,
                       row_number=2, estado=estado)
        if nombre_pc else None
    )
    return HostView(
        host_name=host_name,
        current_state=HostState.UP,
        downtime_seconds=0,
        downtime_human="",
        plugin_output="",
        last_check=AHORA,
        polled_at=AHORA,
        pattern=Pattern.STABLE,
        match_type=MatchType.RAK,
        entry=entry,
        tags=tags or [],
        services=services or [],
    )


# --- Qué cuenta como alarma ---

def test_un_check_en_ok_no_es_una_alarma():
    host = make_host(services=[make_service(state=ServiceState.OK)])
    assert get_alarmas([host], ahora=AHORA) == []


def test_se_listan_criticas_advertencias_y_desconocidas():
    host = make_host(services=[
        make_service(description="CPU Load", state=ServiceState.CRITICAL),
        make_service(description="Disk Space", state=ServiceState.WARNING),
        make_service(description="WWAN Radio", state=ServiceState.UNKNOWN),
        make_service(description="RAM Usage", state=ServiceState.OK),
    ])
    tipos = {a.tipo for a in get_alarmas([host], ahora=AHORA)}
    assert tipos == {"CPU Load", "Disk Space", "WWAN Radio"}


@pytest.mark.parametrize("estado", [
    EstadoOperativo.RMA,
    EstadoOperativo.TESTING,
    EstadoOperativo.REFURBISH,
    EstadoOperativo.BAJA,
])
def test_los_equipos_fuera_de_produccion_no_generan_alarmas(estado):
    """Están fuera de servicio a propósito: sus alarmas no son accionables."""
    host = make_host(services=[make_service(state=ServiceState.CRITICAL)], estado=estado)
    assert get_alarmas([host], ahora=AHORA) == []


def test_un_equipo_en_produccion_con_tag_de_rma_igual_alarma():
    """El tag ya no decide: manda la columna Estado del inventario.

    Es el caso Cardenas 2207 — salió del refurbish pero el tag quedó pegado.
    """
    host = make_host(
        services=[make_service(state=ServiceState.CRITICAL)],
        estado=EstadoOperativo.PRODUCCION,
        tags=[Tag(id=1, name="Refurbish", color="#f00", category="refurbish")],
    )
    assert len(get_alarmas([host], ahora=AHORA)) == 1


def test_sin_ficha_de_inventario_el_tag_sigue_excluyendo():
    """Fallback: sin fila en el inventario no hay columna Estado donde mirar."""
    host = make_host(
        services=[make_service(state=ServiceState.CRITICAL)],
        nombre_pc=None,
        tags=[Tag(id=1, name="RMA", color="#f00", category="rma")],
    )
    assert get_alarmas([host], ahora=AHORA) == []


def test_la_alarma_lleva_el_nombre_real_del_equipo_y_su_ambiente():
    host = make_host(services=[make_service()], nombre_pc="Av. Monroe 1231", ambiente="GCBA")
    alarma = get_alarmas([host], ahora=AHORA)[0]
    assert alarma.nombre_pc == "Av. Monroe 1231"
    assert alarma.ambiente == "GCBA"


def test_un_equipo_sin_ficha_de_inventario_igual_alarma():
    """Sin match no se descarta: se muestra con el host_name crudo."""
    host = make_host(services=[make_service()], nombre_pc=None)
    alarma = get_alarmas([host], ahora=AHORA)[0]
    assert alarma.nombre_pc is None
    assert alarma.host_name == "HOST1"


# --- La regla de las 48 horas ---

def test_critica_de_mas_de_48h_requiere_intervencion():
    host = make_host(services=[make_service(state=ServiceState.CRITICAL, desde_horas=49)])
    assert get_alarmas([host], ahora=AHORA)[0].requiere_intervencion is True


def test_critica_reciente_no_requiere_intervencion():
    host = make_host(services=[make_service(state=ServiceState.CRITICAL, desde_horas=5)])
    assert get_alarmas([host], ahora=AHORA)[0].requiere_intervencion is False


def test_justo_en_el_umbral_cuenta():
    host = make_host(services=[
        make_service(state=ServiceState.CRITICAL, desde_horas=UMBRAL_INTERVENCION_SEGUNDOS / HORA)
    ])
    assert get_alarmas([host], ahora=AHORA)[0].requiere_intervencion is True


@pytest.mark.parametrize("estado", [ServiceState.WARNING, ServiceState.UNKNOWN])
def test_una_advertencia_vieja_no_entra_en_la_bandeja(estado):
    """Se eligió deliberadamente dejar los WARNING crónicos afuera: hoy hay 26
    de disco y la bandeja nacería inservible."""
    host = make_host(services=[make_service(state=estado, desde_horas=500)])
    assert get_intervenciones([host], ahora=AHORA) == []


def test_la_bandeja_solo_trae_lo_sostenido():
    host = make_host(services=[
        make_service(description="CPU Load", state=ServiceState.CRITICAL, desde_horas=100),
        make_service(description="Disk Space", state=ServiceState.CRITICAL, desde_horas=2),
        make_service(description="RAM Usage", state=ServiceState.WARNING, desde_horas=900),
    ])
    assert [a.tipo for a in get_intervenciones([host], ahora=AHORA)] == ["CPU Load"]


# --- Orden de atención ---

def test_primero_lo_que_requiere_intervencion_despues_por_gravedad():
    host = make_host(services=[
        make_service(description="WWAN Radio", state=ServiceState.UNKNOWN, desde_horas=1),
        make_service(description="Disk Space", state=ServiceState.WARNING, desde_horas=1),
        make_service(description="RAM Usage", state=ServiceState.CRITICAL, desde_horas=1),
        make_service(description="CPU Load", state=ServiceState.CRITICAL, desde_horas=200),
    ])
    assert [a.tipo for a in get_alarmas([host], ahora=AHORA)] == [
        "CPU Load",     # crítica sostenida: va primero
        "RAM Usage",    # crítica reciente
        "Disk Space",   # advertencia
        "WWAN Radio",   # desconocida
    ]


def test_a_igual_gravedad_primero_la_mas_vieja():
    host = make_host(services=[
        make_service(description="CPU Load", state=ServiceState.WARNING, desde_horas=2),
        make_service(description="Disk Space", state=ServiceState.WARNING, desde_horas=20),
    ])
    assert [a.tipo for a in get_alarmas([host], ahora=AHORA)] == ["Disk Space", "CPU Load"]


def test_la_duracion_se_muestra_legible():
    host = make_host(services=[make_service(desde_horas=49)])
    assert get_alarmas([host], ahora=AHORA)[0].duracion_human == "2d 1h 0m"


# --- Catálogo y resumen por tipo ---

def test_el_catalogo_cubre_los_siete_servicios_del_agente():
    esperados = {
        "CPU Load", "RAM Usage", "Disk Space", "WWAN Radio",
        "Critical Processes", "Temperature", "Security Events",
    }
    assert {t.nombre for t in TIPOS_ALARMA} == esperados


def test_los_tipos_conocidos_tienen_nombre_en_castellano():
    assert etiqueta_de("Disk Space") == "Espacio en disco"


def test_un_tipo_desconocido_se_muestra_tal_cual():
    """Si el agente suma un servicio nuevo, no se rompe ni se oculta."""
    assert etiqueta_de("Network Bandwidth") == "Network Bandwidth"


def test_el_resumen_incluye_los_tipos_en_cero():
    """Que un tipo desaparezca al estar sano obliga a recordar qué se vigila."""
    host = make_host(services=[make_service(description="CPU Load", state=ServiceState.CRITICAL)])
    resumen = {r.tipo: r for r in resumir_por_tipo(get_alarmas([host], ahora=AHORA))}

    assert resumen["CPU Load"].critical == 1
    assert resumen["Temperature"].total == 0
    assert len(resumen) == len(TIPOS_ALARMA)


def test_el_resumen_cuenta_por_gravedad():
    hosts = [
        make_host(host_name="H1", services=[make_service(description="Disk Space", state=ServiceState.WARNING)]),
        make_host(host_name="H2", services=[make_service(description="Disk Space", state=ServiceState.CRITICAL, desde_horas=99)]),
    ]
    disco = {r.tipo: r for r in resumir_por_tipo(get_alarmas(hosts, ahora=AHORA))}["Disk Space"]
    assert (disco.warning, disco.critical, disco.requieren_intervencion) == (1, 1, 1)


def test_un_tipo_fuera_del_catalogo_se_suma_al_final():
    host = make_host(services=[make_service(description="Network Bandwidth", state=ServiceState.CRITICAL)])
    resumen = resumir_por_tipo(get_alarmas([host], ahora=AHORA))
    assert resumen[-1].tipo == "Network Bandwidth"


# --- Agentes mudos ---

def test_un_equipo_con_todas_las_alarmas_desconocidas_es_un_agente_mudo():
    host = make_host(services=[
        make_service(description="CPU Load", state=ServiceState.UNKNOWN),
        make_service(description="Disk Space", state=ServiceState.UNKNOWN),
    ])
    assert detectar_agentes_mudos([host]) == [host]


def test_si_alguna_alarma_reporta_el_agente_no_esta_mudo():
    host = make_host(services=[
        make_service(description="CPU Load", state=ServiceState.UNKNOWN),
        make_service(description="Disk Space", state=ServiceState.OK),
    ])
    assert detectar_agentes_mudos([host]) == []


def test_un_equipo_sin_servicios_no_cuenta_como_mudo():
    """Nunca reportó nada: es un alta incompleta, no un agente que se calló."""
    assert detectar_agentes_mudos([make_host(services=[])]) == []
