from app.db import (
    get_all_host_names,
    get_all_service_statuses,
    get_all_replacements,
    get_all_sim_consumption,
    get_connection,
    get_host_history,
    get_last_poll_attempt,
    get_latest_snapshots,
    get_replacement_chain,
    record_host_replacement,
    record_poll_attempt,
    save_snapshot,
    upsert_service_statuses,
    upsert_sim_consumption,
)
from app.status_parser import HostState, HostStatus, ServiceState, ServiceStatus


class FakeSimUsage:
    def __init__(self, consumo_mb, limite_mb):
        self.consumo_mb = consumo_mb
        self.limite_mb = limite_mb


def make_service(host_name="HOST1", description="CPU Load", state=ServiceState.OK):
    return ServiceStatus(
        host_name=host_name, service_description=description, current_state=state,
        last_check=1000, last_state_change=900, plugin_output="test",
    )


def make_host(name="HOST1", state=HostState.DOWN, last_state_change=1000):
    return HostStatus(
        host_name=name,
        current_state=state,
        last_check=1000,
        last_state_change=last_state_change,
        plugin_output="test",
        last_time_up=0,
        last_time_down=1000,
    )


def test_save_and_retrieve_snapshot(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host()], polled_at=1000)

    history = get_host_history(conn, "HOST1")
    assert len(history) == 1
    assert history[0].current_state == HostState.DOWN
    assert history[0].polled_at == 1000
    conn.close()


def test_history_ordered_oldest_first(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host(state=HostState.DOWN)], polled_at=2000)
    save_snapshot(conn, [make_host(state=HostState.UP)], polled_at=1000)

    history = get_host_history(conn, "HOST1")
    assert [s.polled_at for s in history] == [1000, 2000]
    conn.close()


def test_history_filters_by_since(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host()], polled_at=1000)
    save_snapshot(conn, [make_host()], polled_at=5000)

    history = get_host_history(conn, "HOST1", since=4000)
    assert [s.polled_at for s in history] == [5000]
    conn.close()


def test_history_scoped_per_host(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host(name="HOST1"), make_host(name="HOST2")], polled_at=1000)

    assert len(get_host_history(conn, "HOST1")) == 1
    assert len(get_host_history(conn, "HOST2")) == 1
    assert get_host_history(conn, "HOST3") == []
    conn.close()


def test_get_all_host_names_is_distinct(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host(name="HOST1")], polled_at=1000)
    save_snapshot(conn, [make_host(name="HOST1")], polled_at=2000)
    save_snapshot(conn, [make_host(name="HOST2")], polled_at=1000)

    assert sorted(get_all_host_names(conn)) == ["HOST1", "HOST2"]
    conn.close()


def test_reopening_existing_db_preserves_schema(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn1 = get_connection(db_path)
    save_snapshot(conn1, [make_host()], polled_at=1000)
    conn1.close()

    conn2 = get_connection(db_path)
    history = get_host_history(conn2, "HOST1")
    assert len(history) == 1
    conn2.close()


def test_record_poll_attempt_success(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    record_poll_attempt(conn, attempted_at=1000, success=True, host_count=212)

    attempt = get_last_poll_attempt(conn)
    assert attempt.success is True
    assert attempt.host_count == 212
    assert attempt.error_message is None
    conn.close()


def test_record_poll_attempt_failure(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    record_poll_attempt(conn, attempted_at=1000, success=False, error_message="timeout")

    attempt = get_last_poll_attempt(conn)
    assert attempt.success is False
    assert attempt.error_message == "timeout"
    assert attempt.host_count is None
    conn.close()


def test_get_last_poll_attempt_returns_most_recent(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    record_poll_attempt(conn, attempted_at=1000, success=True, host_count=100)
    record_poll_attempt(conn, attempted_at=2000, success=False, error_message="fallo reciente")

    attempt = get_last_poll_attempt(conn)
    assert attempt.attempted_at == 2000
    assert attempt.success is False
    conn.close()


def test_get_last_poll_attempt_returns_none_when_empty(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    assert get_last_poll_attempt(conn) is None
    conn.close()


def test_upsert_service_statuses_inserts_new(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    upsert_service_statuses(conn, [make_service()], polled_at=1000)

    rows = get_all_service_statuses(conn)
    assert len(rows) == 1
    assert rows[0].host_name == "HOST1"
    assert rows[0].current_state == ServiceState.OK
    conn.close()


def test_upsert_service_statuses_overwrites_not_duplicates(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    upsert_service_statuses(conn, [make_service(state=ServiceState.OK)], polled_at=1000)
    upsert_service_statuses(conn, [make_service(state=ServiceState.CRITICAL)], polled_at=2000)

    rows = get_all_service_statuses(conn)
    assert len(rows) == 1  # no se acumula historial, se pisa
    assert rows[0].current_state == ServiceState.CRITICAL
    assert rows[0].polled_at == 2000
    conn.close()


def test_upsert_service_statuses_keeps_distinct_services_per_host(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    upsert_service_statuses(
        conn,
        [make_service(description="CPU Load"), make_service(description="Disk Space", state=ServiceState.WARNING)],
        polled_at=1000,
    )

    rows = get_all_service_statuses(conn)
    assert len(rows) == 2
    conn.close()


def test_migrates_tags_table_missing_category_column(tmp_path):
    """Simula una base creada antes de agregar `category` a `tags` — no debe romper."""
    import sqlite3

    db_path = str(tmp_path / "test.db")
    old_conn = sqlite3.connect(db_path)
    old_conn.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL)")
    old_conn.execute("INSERT INTO tags (name, color) VALUES ('Urgente', '#ff0000')")
    old_conn.commit()
    old_conn.close()

    conn = get_connection(db_path)  # corre init_schema -> _migrate_schema

    columns = [row[1] for row in conn.execute("PRAGMA table_info(tags)").fetchall()]
    assert "category" in columns

    row = conn.execute("SELECT name, category FROM tags").fetchone()
    assert row == ("Urgente", None)  # la fila preexistente sigue intacta
    conn.close()


def _replace(conn, old_rak, new_rak, old_host, new_host, at):
    record_host_replacement(
        conn, old_rak=old_rak, new_rak=new_rak, old_host_name=old_host,
        new_host_name=new_host, nombre_pc="Sitio", ambiente="GCBA", replaced_at=at,
    )


def test_record_and_read_host_replacement(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    _replace(conn, "RAKVIEJO0000001", "RAKNUEVO0000001", "RAKVIEJO0000001", "RAKNUEVO0000001", 1000)

    cadena = get_replacement_chain(conn, "RAKNUEVO0000001")
    assert len(cadena) == 1
    assert cadena[0].old_rak == "RAKVIEJO0000001"
    assert cadena[0].nombre_pc == "Sitio"
    conn.close()


def test_replacement_chain_follows_multiple_replacements(tmp_path):
    """Un sitio reemplazado dos veces devuelve los dos equipos anteriores, del más nuevo al más viejo."""
    conn = get_connection(str(tmp_path / "test.db"))
    _replace(conn, "RAK_A", "RAK_B", "HOSTA", "HOSTB", 1000)
    _replace(conn, "RAK_B", "RAK_C", "HOSTB", "HOSTC", 2000)

    cadena = get_replacement_chain(conn, "HOSTC")
    assert [r.old_rak for r in cadena] == ["RAK_B", "RAK_A"]
    conn.close()


def test_replacement_chain_empty_for_host_never_replaced(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    assert get_replacement_chain(conn, "HOSTSINHISTORIA") == []
    conn.close()


def test_replacement_chain_survives_a_loop(tmp_path):
    """Si los datos quedaran circulares, la consulta corta en vez de colgarse."""
    conn = get_connection(str(tmp_path / "test.db"))
    _replace(conn, "RAK_A", "RAK_B", "HOSTB", "HOSTA", 1000)
    _replace(conn, "RAK_B", "RAK_A", "HOSTA", "HOSTB", 2000)

    cadena = get_replacement_chain(conn, "HOSTA")
    assert len(cadena) <= 3
    conn.close()


def test_get_all_replacements_newest_first(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    _replace(conn, "RAK_A", "RAK_B", "HOSTA", "HOSTB", 1000)
    _replace(conn, "RAK_C", "RAK_D", "HOSTC", "HOSTD", 3000)

    todos = get_all_replacements(conn)
    assert [r.replaced_at for r in todos] == [3000, 1000]
    conn.close()


def test_upsert_sim_consumption_inserts_new(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    upsert_sim_consumption(conn, {"ICC1": FakeSimUsage(1234.5, 10240.0)}, updated_at=1000)

    rows = get_all_sim_consumption(conn)
    assert rows["ICC1"].consumo_mb == 1234.5
    assert rows["ICC1"].limite_mb == 10240.0
    assert rows["ICC1"].updated_at == 1000
    conn.close()


def test_upsert_sim_consumption_overwrites_not_duplicates(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    upsert_sim_consumption(conn, {"ICC1": FakeSimUsage(1000.0, 10240.0)}, updated_at=1000)
    upsert_sim_consumption(conn, {"ICC1": FakeSimUsage(2000.0, 10240.0)}, updated_at=2000)

    rows = get_all_sim_consumption(conn)
    assert len(rows) == 1
    assert rows["ICC1"].consumo_mb == 2000.0
    assert rows["ICC1"].updated_at == 2000
    conn.close()


def test_upsert_sim_consumption_allows_null_limite(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    upsert_sim_consumption(conn, {"ICC1": FakeSimUsage(500.0, None)}, updated_at=1000)

    rows = get_all_sim_consumption(conn)
    assert rows["ICC1"].limite_mb is None
    conn.close()


def test_get_all_sim_consumption_empty_when_none_saved(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    assert get_all_sim_consumption(conn) == {}
    conn.close()


# --- Hosts dados de baja en Nagios ---
# Al borrar el .cfg de un equipo deja de venir en status.dat, pero su última
# snapshot queda en la base: sin filtro seguiría figurando en el panel.

def test_latest_snapshots_excluye_hosts_que_dejaron_de_reportar(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host(name="VIGENTE"), make_host(name="DADO_DE_BAJA")], polled_at=1000)
    # Varios polls después, solo sigue apareciendo uno
    for t in (2000, 3000, 4000):
        save_snapshot(conn, [make_host(name="VIGENTE")], polled_at=t)

    latest = get_latest_snapshots(conn, stale_after_seconds=1800)
    assert "VIGENTE" in latest
    assert "DADO_DE_BAJA" not in latest
    conn.close()


def test_latest_snapshots_tolera_un_poll_incompleto(tmp_path):
    """Si status.dat se leyó truncado y faltó un host, no debe desaparecer del panel."""
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host(name="HOST1"), make_host(name="HOST2")], polled_at=1000)
    save_snapshot(conn, [make_host(name="HOST1")], polled_at=1600)  # HOST2 faltó una vez

    latest = get_latest_snapshots(conn, stale_after_seconds=1800)
    assert "HOST2" in latest
    conn.close()


def test_latest_snapshots_no_vacia_la_vista_si_el_poller_se_cayo(tmp_path):
    """El corte se mide contra el último poll, no contra la hora actual."""
    conn = get_connection(str(tmp_path / "test.db"))
    save_snapshot(conn, [make_host(name="HOST1"), make_host(name="HOST2")], polled_at=1000)

    latest = get_latest_snapshots(conn, stale_after_seconds=1800)
    assert len(latest) == 2
    conn.close()


def test_latest_snapshots_sin_datos(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    assert get_latest_snapshots(conn) == {}
    conn.close()


# --- Historial por lote (optimización de la vista principal) ---

def test_get_histories_since_agrupa_por_host(tmp_path):
    from app.db import get_histories_since
    conn = get_connection(tmp_path / "t.db")
    save_snapshot(conn, [make_host("H1", HostState.UP), make_host("H2", HostState.DOWN)], polled_at=1000)
    save_snapshot(conn, [make_host("H1", HostState.DOWN)], polled_at=2000)

    historias = get_histories_since(conn, since=0)

    assert set(historias) == {"H1", "H2"}
    assert len(historias["H1"]) == 2
    assert len(historias["H2"]) == 1


def test_get_histories_since_ordena_del_mas_viejo_al_mas_nuevo(tmp_path):
    """`classify` lee history[-1] como el estado actual: el orden es parte del contrato."""
    from app.db import get_histories_since
    conn = get_connection(tmp_path / "t.db")
    save_snapshot(conn, [make_host("H1", HostState.DOWN)], polled_at=3000)
    save_snapshot(conn, [make_host("H1", HostState.UP)], polled_at=1000)
    save_snapshot(conn, [make_host("H1", HostState.DOWN)], polled_at=2000)

    historia = get_histories_since(conn, since=0)["H1"]

    assert [s.polled_at for s in historia] == [1000, 2000, 3000]


def test_get_histories_since_recorta_por_fecha(tmp_path):
    from app.db import get_histories_since
    conn = get_connection(tmp_path / "t.db")
    save_snapshot(conn, [make_host("H1", HostState.UP)], polled_at=1000)
    save_snapshot(conn, [make_host("H1", HostState.DOWN)], polled_at=5000)

    assert len(get_histories_since(conn, since=4000)["H1"]) == 1


def test_get_histories_since_sin_datos_devuelve_vacio(tmp_path):
    from app.db import get_histories_since
    conn = get_connection(tmp_path / "t.db")
    assert get_histories_since(conn, since=0) == {}


def test_get_histories_since_coincide_con_la_consulta_por_host(tmp_path):
    """La versión por lote tiene que dar exactamente lo mismo que la vieja."""
    from app.db import get_histories_since
    conn = get_connection(tmp_path / "t.db")
    for t in (1000, 2000, 3000):
        save_snapshot(conn, [make_host("H1", HostState.UP), make_host("H2", HostState.DOWN)], polled_at=t)

    historias = get_histories_since(conn, since=0)
    for host in ("H1", "H2"):
        assert historias[host] == get_host_history(conn, host)
