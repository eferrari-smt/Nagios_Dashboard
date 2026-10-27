import dataclasses

from app import dashboard_service
from app.dashboard_service import HostView, get_insights, get_pending_inventory_hosts, is_operationally_excluded
from app.db import get_connection, save_snapshot
from app.inventory_loader import InventoryEntry
from app.matching import MatchType
from app.pattern_classifier import Pattern
from app.status_parser import HostState, HostStatus
from app.tags_store import TAG_CATEGORY_REFURBISH, TAG_CATEGORY_RMA, TAG_CATEGORY_TESTING, Tag, assign_tag, create_tag


class _ConexionFalsa:
    def close(self):
        pass


def make_snapshot_host(name, state):
    return HostStatus(
        host_name=name, current_state=state, last_check=1000, last_state_change=1000,
        plugin_output="test", last_time_up=0, last_time_down=1000,
    )


def make_host_view(host_name, state, tags):
    return HostView(
        host_name=host_name, current_state=state, downtime_seconds=0, downtime_human="-",
        plugin_output="", last_check=1000, polled_at=1000, pattern=Pattern.STABLE,
        match_type=MatchType.NO_MATCH, entry=None, tags=tags, services=[],
    )


def test_is_operationally_excluded_true_for_rma_tag():
    host = make_host_view("H1", HostState.DOWN, tags=[Tag(id=1, name="RMA", color="#888", category=TAG_CATEGORY_RMA)])
    assert is_operationally_excluded(host) is True


def test_is_operationally_excluded_true_for_refurbish_tag():
    host = make_host_view("H1", HostState.DOWN, tags=[Tag(id=1, name="Refurbish", color="#fff700", category=TAG_CATEGORY_REFURBISH)])
    assert is_operationally_excluded(host) is True


def test_is_operationally_excluded_true_for_testing_tag():
    host = make_host_view("H1", HostState.DOWN, tags=[Tag(id=1, name="Testing", color="#888", category=TAG_CATEGORY_TESTING)])
    assert is_operationally_excluded(host) is True


def test_is_operationally_excluded_false_for_normal_tag():
    host = make_host_view("H1", HostState.DOWN, tags=[Tag(id=1, name="Urgente", color="#f00", category=None)])
    assert is_operationally_excluded(host) is False


def test_is_operationally_excluded_false_when_no_tags():
    host = make_host_view("H1", HostState.DOWN, tags=[])
    assert is_operationally_excluded(host) is False


def test_get_insights_usa_el_mismo_criterio_que_el_resto(monkeypatch):
    """Los totales grandes tienen que cerrar con las tarjetas por ambiente.

    Antes Insights excluía por tag mientras el resto ya miraba el estado del
    inventario, y el número grande mostraba 222 contra 225 de las tarjetas. Un
    panel cuyos números no cierran no sirve para decidir nada.
    """
    from app.inventory_loader import EstadoOperativo

    def ficha(nombre, estado):
        return InventoryEntry(nombre_pc=nombre, rak="RAK" + nombre, anydesk_id="1",
                              cliente="GCBA", row_number=2, estado=estado)

    vistas = [
        make_host_view("H1", HostState.UP, tags=[]),
        make_host_view("H2", HostState.DOWN, tags=[]),
        make_host_view("H3", HostState.DOWN, tags=[]),
    ]
    vistas[0] = dataclasses.replace(vistas[0], entry=ficha("H1", EstadoOperativo.PRODUCCION))
    vistas[1] = dataclasses.replace(vistas[1], entry=ficha("H2", EstadoOperativo.RMA))
    vistas[2] = dataclasses.replace(vistas[2], entry=ficha("H3", EstadoOperativo.PRODUCCION))

    monkeypatch.setattr(dashboard_service, "get_current_hosts", lambda: vistas)
    monkeypatch.setattr(dashboard_service, "get_all_service_statuses", lambda conn: [])
    monkeypatch.setattr(dashboard_service, "get_connection", lambda path: _ConexionFalsa())

    insights = get_insights()

    assert insights.host_up == 1
    assert insights.host_down == 1  # solo H3: H2 está en RMA


def test_get_insights_el_tag_sigue_valiendo_sin_ficha(monkeypatch):
    """Un host sin fila en el inventario no tiene estado: ahí manda el tag."""
    vistas = [
        make_host_view("H1", HostState.DOWN, tags=[]),
        make_host_view("H2", HostState.DOWN,
                       tags=[Tag(id=1, name="RMA", color="#888", category=TAG_CATEGORY_RMA)]),
    ]
    monkeypatch.setattr(dashboard_service, "get_current_hosts", lambda: vistas)
    monkeypatch.setattr(dashboard_service, "get_all_service_statuses", lambda conn: [])
    monkeypatch.setattr(dashboard_service, "get_connection", lambda path: _ConexionFalsa())

    assert get_insights().host_down == 1


def make_entry(nombre_pc, rak="", cliente="STOCK", row_number=2):
    return InventoryEntry(nombre_pc=nombre_pc, rak=rak, anydesk_id="1", cliente=cliente, row_number=row_number)


def test_get_pending_inventory_hosts_excludes_known_nagios_hosts(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dashboard_service, "load_database_path", lambda: db_path)

    # KNOWNRAK123456789 truncado a 15 = "KNOWNRAK1234567" -> lo marcamos como ya conocido
    conn = get_connection(db_path)
    from app.status_parser import HostStatus
    save_snapshot(conn, [HostStatus(
        host_name="KNOWNRAK1234567", current_state=HostState.UP, last_check=1000,
        last_state_change=1000, plugin_output="", last_time_up=1000, last_time_down=0,
    )], polled_at=1000)
    conn.close()

    entries = [
        make_entry("Equipo Conocido", rak="KNOWNRAK123456789"),  # ya reporta -> no debe aparecer
        make_entry("Equipo En Stock", rak="STOCKRAK000000001"),  # nunca reportó -> pendiente
    ]
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: entries)

    pending = get_pending_inventory_hosts()

    assert len(pending) == 1
    assert pending[0].nombre_pc == "Equipo En Stock"


def test_get_pending_inventory_hosts_falls_back_to_nombre_pc_when_rak_empty(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dashboard_service, "load_database_path", lambda: db_path)
    get_connection(db_path).close()

    entries = [make_entry("STOCKID000000000001", rak="")]
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: entries)

    pending = get_pending_inventory_hosts()

    assert len(pending) == 1
    assert pending[0].host_name == "STOCKID000000000001"[:15].upper()


def test_get_pending_inventory_hosts_skips_entries_with_no_usable_identifier(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dashboard_service, "load_database_path", lambda: db_path)
    get_connection(db_path).close()

    entries = [make_entry("---", rak="")]
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: entries)

    assert get_pending_inventory_hosts() == []


def test_get_pending_inventory_hosts_includes_assigned_tags(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dashboard_service, "load_database_path", lambda: db_path)

    conn = get_connection(db_path)
    tag = create_tag(conn, "RMA", "#888888", category=TAG_CATEGORY_RMA)
    assign_tag(conn, tag.id, ["STOCKRAK0000000"])  # host_name ya truncado a 15, como lo derive_host_name
    conn.close()

    entries = [make_entry("Equipo En Stock", rak="STOCKRAK000000001")]
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: entries)

    pending = get_pending_inventory_hosts()

    assert len(pending) == 1
    assert pending[0].tags[0].name == "RMA"


def test_get_pending_inventory_hosts_dedupes_same_derived_name(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dashboard_service, "load_database_path", lambda: db_path)
    get_connection(db_path).close()

    entries = [
        make_entry("Fila A", rak="SAMERAK000000000001"),
        make_entry("Fila B", rak="SAMERAK000000000002"),  # mismos primeros 15 -> mismo host_name
    ]
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: entries)

    pending = get_pending_inventory_hosts()

    assert len(pending) == 1
