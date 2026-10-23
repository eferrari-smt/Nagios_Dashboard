"""Historial de alarmas (`service_events`).

Lo que se prueba acá es que la línea de tiempo registre exactamente los
cambios y nada más: si cada poll agregara una fila por alarma sin filtrar,
serían ~230.000 filas por día y el histórico sería inusable.
"""

from app.db import (
    get_connection,
    get_service_events,
    upsert_service_statuses,
)
from app.status_parser import ServiceState, ServiceStatus


def make_service(
    host_name="HOST1",
    description="CPU Load",
    state=ServiceState.OK,
    last_state_change=900,
    plugin_output="test",
):
    return ServiceStatus(
        host_name=host_name,
        service_description=description,
        current_state=state,
        last_check=1000,
        last_state_change=last_state_change,
        plugin_output=plugin_output,
    )


# --- Qué se registra y qué no ---

def test_la_primera_vez_que_se_ve_una_alarma_queda_como_alta(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    transiciones = upsert_service_statuses(conn, [make_service()], polled_at=1000)

    assert transiciones == 1
    eventos = get_service_events(conn)
    assert len(eventos) == 1
    # Alta, no transición: no había estado anterior del cual venir.
    assert eventos[0].previous_state is None
    assert eventos[0].current_state == ServiceState.OK


def test_un_estado_que_no_cambia_no_genera_evento(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK)], polled_at=1000)
    transiciones = upsert_service_statuses(conn, [make_service(state=ServiceState.OK)], polled_at=2000)

    assert transiciones == 0
    # Sigue estando solo el alta: 10 polls con la misma alarma en OK no son 10 filas.
    assert len(get_service_events(conn)) == 1


def test_un_cambio_de_estado_registra_de_donde_venia(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK)], polled_at=1000)
    upsert_service_statuses(conn, [make_service(state=ServiceState.CRITICAL)], polled_at=2000)

    eventos = get_service_events(conn)
    assert len(eventos) == 2
    ultimo = eventos[0]
    assert ultimo.previous_state == ServiceState.OK
    assert ultimo.current_state == ServiceState.CRITICAL


def test_ida_y_vuelta_deja_las_dos_transiciones(tmp_path):
    """Un equipo que se degrada y se recupera tiene que verse entero en la ficha."""
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK)], polled_at=1000)
    upsert_service_statuses(conn, [make_service(state=ServiceState.WARNING, last_state_change=1500)], polled_at=2000)
    upsert_service_statuses(conn, [make_service(state=ServiceState.CRITICAL, last_state_change=2500)], polled_at=3000)
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK, last_state_change=3500)], polled_at=4000)

    eventos = get_service_events(conn)
    recorrido = [(e.previous_state, e.current_state) for e in reversed(eventos)]
    assert recorrido == [
        (None, ServiceState.OK),
        (ServiceState.OK, ServiceState.WARNING),
        (ServiceState.WARNING, ServiceState.CRITICAL),
        (ServiceState.CRITICAL, ServiceState.OK),
    ]


def test_usa_el_momento_del_cambio_de_nagios_y_no_el_del_poll(tmp_path):
    """La alarma cambió a las 1500; el poll la vio a las 2000. Vale 1500."""
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK)], polled_at=1000)
    upsert_service_statuses(
        conn, [make_service(state=ServiceState.CRITICAL, last_state_change=1500)], polled_at=2000
    )

    evento = get_service_events(conn)[0]
    assert evento.changed_at == 1500
    assert evento.recorded_at == 2000


def test_cada_alarma_del_mismo_equipo_se_sigue_por_separado(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [
        make_service(description="CPU Load", state=ServiceState.OK),
        make_service(description="Disk Space", state=ServiceState.OK),
    ], polled_at=1000)
    # Solo cambia el disco; la CPU no debería generar nada.
    transiciones = upsert_service_statuses(conn, [
        make_service(description="CPU Load", state=ServiceState.OK),
        make_service(description="Disk Space", state=ServiceState.WARNING),
    ], polled_at=2000)

    assert transiciones == 1
    assert get_service_events(conn, service_description="CPU Load")[0].previous_state is None
    assert get_service_events(conn, service_description="Disk Space")[0].current_state == ServiceState.WARNING


def test_equipos_distintos_no_se_mezclan(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [
        make_service(host_name="HOST1", state=ServiceState.OK),
        make_service(host_name="HOST2", state=ServiceState.OK),
    ], polled_at=1000)
    upsert_service_statuses(conn, [
        make_service(host_name="HOST1", state=ServiceState.CRITICAL),
        make_service(host_name="HOST2", state=ServiceState.OK),
    ], polled_at=2000)

    assert len(get_service_events(conn, host_name="HOST1")) == 2
    assert len(get_service_events(conn, host_name="HOST2")) == 1


# --- Consulta ---

def test_los_eventos_vienen_del_mas_nuevo_al_mas_viejo(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    for i, estado in enumerate([ServiceState.OK, ServiceState.WARNING, ServiceState.CRITICAL], start=1):
        upsert_service_statuses(
            conn, [make_service(state=estado, last_state_change=i * 1000)], polled_at=i * 1000
        )

    eventos = get_service_events(conn)
    assert [e.changed_at for e in eventos] == [3000, 2000, 1000]


def test_filtro_por_fecha_para_la_ventana_de_48h(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK, last_state_change=1000)], polled_at=1000)
    upsert_service_statuses(conn, [make_service(state=ServiceState.CRITICAL, last_state_change=5000)], polled_at=5000)

    assert len(get_service_events(conn, since=4000)) == 1
    assert len(get_service_events(conn, since=500)) == 2


def test_el_limite_acota_la_consulta(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    for i in range(1, 6):
        estado = ServiceState.OK if i % 2 else ServiceState.CRITICAL
        upsert_service_statuses(conn, [make_service(state=estado, last_state_change=i * 100)], polled_at=i * 100)

    assert len(get_service_events(conn, limit=2)) == 2


def test_sin_historial_devuelve_lista_vacia(tmp_path):
    """La vista tiene que dibujarse igual el primer día, sin datos todavía."""
    conn = get_connection(tmp_path / "t.db")
    assert get_service_events(conn) == []
