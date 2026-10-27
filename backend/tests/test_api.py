import pytest
from fastapi.testclient import TestClient

from app import api
from app.dashboard_service import (
    DashboardInsights,
    DashboardMeta,
    HostNotEditableError,
    HostView,
    InventoryUnavailableError,
    PendingInventoryHost,
)
from app.db import ServiceStatusRow, SnapshotRow
from app.inventory_loader import InventoryEntry
from app.inventory_writer import InventoryWriteError
from app.matching import DuplicateGroup, MatchType
from app.dashboard_service import (
    HostNagiosInfo,
    HostRegistrationResult,
    MissingInventoryDataError,
    ReplaceResult,
)
from app.nagios_uploader import (
    AddHostPlan,
    AddHostResult,
    InvalidRakError,
    NagiosHostNotFoundError,
    NagiosReloadError,
    NagiosValidationError,
    RemoveHostPlan,
    RemoveHostResult,
)
from app.pattern_classifier import Pattern
from app.ssh_client import NagiosSSHError
from app.status_parser import HostState, ServiceState
from app.tags_store import DuplicateTagNameError, Tag

client = TestClient(api.app)


class _FakeConn:
    """El middleware de sesión abre la base en cada request; acá se evita que
    los tests toquen la base real."""

    def close(self):
        pass


@pytest.fixture(autouse=True)
def sesion_iniciada(monkeypatch):
    """Todos los tests corren como un usuario con sesión válida.

    El control de acceso en sí se prueba aparte, en test_api_auth.py.
    """
    monkeypatch.setattr(api, "get_connection", lambda path: _FakeConn())
    monkeypatch.setattr(api, "get_session_user", lambda conn, token: "enzo")



def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_meta_reflects_service_data(monkeypatch):
    monkeypatch.setattr(
        api, "get_meta",
        lambda: DashboardMeta(
            last_polled_at=1000, host_count=5, down_count=2, unmatched_count=1,
            last_poll_success=True, last_poll_error=None,
        ),
    )
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {
        "last_polled_at": 1000, "host_count": 5, "down_count": 2, "unmatched_count": 1,
        "last_poll_success": True, "last_poll_error": None,
    }


def test_meta_reflects_failed_poll(monkeypatch):
    monkeypatch.setattr(
        api, "get_meta",
        lambda: DashboardMeta(
            last_polled_at=1000, host_count=5, down_count=2, unmatched_count=1,
            last_poll_success=False, last_poll_error="No se pudo conectar a la EC2.",
        ),
    )
    response = client.get("/api/meta")
    body = response.json()
    assert body["last_poll_success"] is False
    assert body["last_poll_error"] == "No se pudo conectar a la EC2."


def test_hosts_returns_matched_and_unmatched(monkeypatch):
    entry = InventoryEntry(
        nombre_pc="Av. Monroe 1231", rak="ABCDEF1234567890XYZ", anydesk_id="123456",
        cliente="GCBA", row_number=2,
    )
    views = [
        HostView(
            host_name="HOST1", current_state=HostState.DOWN, downtime_seconds=3600,
            downtime_human="1h 0m", plugin_output="CRITICAL", last_check=1000, polled_at=1000,
            pattern=Pattern.RECENT, match_type=MatchType.RAK, entry=entry, tags=[], services=[],
        ),
        HostView(
            host_name="HOST2", current_state=HostState.DOWN, downtime_seconds=0,
            downtime_human="-", plugin_output="", last_check=1000, polled_at=1000,
            pattern=Pattern.STABLE, match_type=MatchType.NO_MATCH, entry=None, tags=[], services=[],
        ),
    ]
    monkeypatch.setattr(api, "get_current_hosts", lambda: views)

    response = client.get("/api/hosts")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["nombre_pc"] == "Av. Monroe 1231"
    assert body[1]["nombre_pc"] is None
    assert body[1]["match_type"] == "no_match"


def test_hosts_returns_503_when_inventory_unavailable(monkeypatch):
    def raise_error():
        raise InventoryUnavailableError("archivo no encontrado")

    monkeypatch.setattr(api, "get_current_hosts", raise_error)
    response = client.get("/api/hosts")
    assert response.status_code == 503


def test_host_history_returns_snapshots(monkeypatch):
    history = [
        SnapshotRow(host_name="HOST1", current_state=HostState.DOWN, last_check=1000,
                    last_state_change=900, plugin_output="down", polled_at=1000),
        SnapshotRow(host_name="HOST1", current_state=HostState.UP, last_check=2000,
                    last_state_change=2000, plugin_output="up", polled_at=2000),
    ]
    monkeypatch.setattr(api, "get_host_history_view", lambda host_name, since=None: history)

    response = client.get("/api/hosts/HOST1/history")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["current_state"] == "DOWN"
    assert body[1]["current_state"] == "UP"


def test_host_history_returns_404_when_empty(monkeypatch):
    monkeypatch.setattr(api, "get_host_history_view", lambda host_name, since=None: [])
    response = client.get("/api/hosts/UNKNOWN/history")
    assert response.status_code == 404


def test_inventory_duplicates_serializes_groups(monkeypatch):
    entries = [
        InventoryEntry(nombre_pc="A", rak="RAK1", anydesk_id="111", cliente="GCBA", row_number=2),
        InventoryEntry(nombre_pc="B", rak="RAK1", anydesk_id="111", cliente="RESTO", row_number=8),
    ]
    groups = [DuplicateGroup(rak_normalized="RAK1", anydesk_id="111", entries=entries)]
    monkeypatch.setattr(api, "get_inventory_duplicate_groups", lambda: groups)

    response = client.get("/api/inventory/duplicates")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["anydesk_id"] == "111"
    assert len(body[0]["entries"]) == 2


def test_inventory_duplicates_returns_503_when_inventory_unavailable(monkeypatch):
    def raise_error():
        raise InventoryUnavailableError("archivo no encontrado")

    monkeypatch.setattr(api, "get_inventory_duplicate_groups", raise_error)
    response = client.get("/api/inventory/duplicates")
    assert response.status_code == 503


def test_hosts_serializes_tags(monkeypatch):
    view = HostView(
        host_name="HOST1", current_state=HostState.UP, downtime_seconds=0,
        downtime_human="-", plugin_output="", last_check=1000, polled_at=1000,
        pattern=Pattern.STABLE, match_type=MatchType.NO_MATCH, entry=None,
        tags=[Tag(id=1, name="Urgente", color="#ff0000")], services=[],
    )
    monkeypatch.setattr(api, "get_current_hosts", lambda: [view])
    response = client.get("/api/hosts")
    body = response.json()
    assert body[0]["tags"] == [{"id": 1, "name": "Urgente", "color": "#ff0000", "category": None}]


def test_list_tags(monkeypatch):
    monkeypatch.setattr(api, "get_tags", lambda: [Tag(id=1, name="Urgente", color="#ff0000")])
    response = client.get("/api/tags")
    assert response.status_code == 200
    assert response.json() == [{"id": 1, "name": "Urgente", "color": "#ff0000", "category": None}]


def test_list_tags_includes_category(monkeypatch):
    monkeypatch.setattr(api, "get_tags", lambda: [Tag(id=1, name="RMA", color="#888888", category="rma")])
    response = client.get("/api/tags")
    assert response.status_code == 200
    assert response.json() == [{"id": 1, "name": "RMA", "color": "#888888", "category": "rma"}]


def test_create_tag(monkeypatch):
    monkeypatch.setattr(api, "create_new_tag", lambda name, color, category: Tag(id=1, name=name, color=color, category=category))
    response = client.post("/api/tags", json={"name": "Urgente", "color": "#ff0000"})
    assert response.status_code == 201
    assert response.json() == {"id": 1, "name": "Urgente", "color": "#ff0000", "category": None}


def test_create_tag_with_category(monkeypatch):
    monkeypatch.setattr(api, "create_new_tag", lambda name, color, category: Tag(id=1, name=name, color=color, category=category))
    response = client.post("/api/tags", json={"name": "RMA", "color": "#888888", "category": "rma"})
    assert response.status_code == 201
    assert response.json()["category"] == "rma"


def test_create_tag_rejects_empty_name():
    response = client.post("/api/tags", json={"name": "   ", "color": "#ff0000"})
    assert response.status_code == 422


def test_create_tag_duplicate_name_returns_409(monkeypatch):
    def raise_dup(name, color, category):
        raise DuplicateTagNameError(f"Ya existe un tag llamado '{name}'.")

    monkeypatch.setattr(api, "create_new_tag", raise_dup)
    response = client.post("/api/tags", json={"name": "Urgente", "color": "#ff0000"})
    assert response.status_code == 409


def test_update_tag_category(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "update_existing_tag_category", lambda tag_id, category: calls.append((tag_id, category)))
    response = client.patch("/api/tags/5", json={"category": "testing"})
    assert response.status_code == 200
    assert calls == [(5, "testing")]


def test_update_tag_category_to_none(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "update_existing_tag_category", lambda tag_id, category: calls.append((tag_id, category)))
    response = client.patch("/api/tags/5", json={"category": None})
    assert response.status_code == 200
    assert calls == [(5, None)]


def test_delete_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "delete_existing_tag", lambda tag_id: calls.append(tag_id))
    response = client.delete("/api/tags/5")
    assert response.status_code == 204
    assert calls == [5]


def test_assign_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "assign_tag_to_hosts", lambda tag_id, hosts: calls.append((tag_id, hosts)))
    response = client.post("/api/tags/3/assign", json={"host_names": ["HOST1", "HOST2"]})
    assert response.status_code == 200
    assert calls == [(3, ["HOST1", "HOST2"])]


def test_unassign_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "unassign_tag_from_hosts", lambda tag_id, hosts: calls.append((tag_id, hosts)))
    response = client.post("/api/tags/3/unassign", json={"host_names": ["HOST1"]})
    assert response.status_code == 200
    assert calls == [(3, ["HOST1"])]


def test_trigger_poll_runs_job_then_returns_fresh_meta(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "run_poll_job", lambda: calls.append("polled"))
    monkeypatch.setattr(
        api, "get_meta",
        lambda: DashboardMeta(
            last_polled_at=2000, host_count=5, down_count=1, unmatched_count=0,
            last_poll_success=True, last_poll_error=None,
        ),
    )
    response = client.post("/api/poll")
    assert response.status_code == 200
    assert calls == ["polled"]
    assert response.json()["last_polled_at"] == 2000


def test_edit_host_updates_nombre_and_notas(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api, "edit_host_inventory",
        lambda host_name, nombre_pc=None, notas=None, sim=None, empresa=None, ambiente=None, estado=None: calls.append(
            (host_name, nombre_pc, notas, sim, empresa, ambiente)
        ),
    )
    response = client.patch("/api/hosts/HOST1", json={"nombre_pc": "Nuevo Nombre", "notas": "Revisar"})
    assert response.status_code == 200
    assert calls == [("HOST1", "Nuevo Nombre", "Revisar", None, None, None)]


def test_edit_host_updates_sim_and_empresa(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api, "edit_host_inventory",
        lambda host_name, nombre_pc=None, notas=None, sim=None, empresa=None, ambiente=None, estado=None: calls.append(
            (host_name, nombre_pc, notas, sim, empresa, ambiente)
        ),
    )
    response = client.patch("/api/hosts/HOST1", json={"sim": "8954000000000000002", "empresa": "Movistar"})
    assert response.status_code == 200
    assert calls == [("HOST1", None, None, "8954000000000000002", "Movistar", None)]


def test_edit_host_updates_ambiente(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api, "edit_host_inventory",
        lambda host_name, nombre_pc=None, notas=None, sim=None, empresa=None, ambiente=None, estado=None: calls.append(
            (host_name, nombre_pc, notas, sim, empresa, ambiente)
        ),
    )
    response = client.patch("/api/hosts/HOST1", json={"ambiente": "Oficina"})
    assert response.status_code == 200
    assert calls == [("HOST1", None, None, None, None, "Oficina")]


def test_edit_host_rejects_empty_payload():
    response = client.patch("/api/hosts/HOST1", json={})
    assert response.status_code == 422


def test_edit_host_returns_404_when_unmatched(monkeypatch):
    def raise_not_editable(host_name, nombre_pc=None, notas=None, sim=None, empresa=None, ambiente=None, estado=None):
        raise HostNotEditableError(f"'{host_name}' no tiene fila propia.")

    monkeypatch.setattr(api, "edit_host_inventory", raise_not_editable)
    response = client.patch("/api/hosts/HOST1", json={"nombre_pc": "X"})
    assert response.status_code == 404


def test_edit_host_returns_503_when_excel_locked(monkeypatch):
    def raise_write_error(host_name, nombre_pc=None, notas=None, sim=None, empresa=None, ambiente=None, estado=None):
        raise InventoryWriteError("archivo abierto en otro programa")

    monkeypatch.setattr(api, "edit_host_inventory", raise_write_error)
    response = client.patch("/api/hosts/HOST1", json={"nombre_pc": "X"})
    assert response.status_code == 503


def test_hosts_serializes_services(monkeypatch):
    service = ServiceStatusRow(
        host_name="HOST1", service_description="CPU Load", current_state=ServiceState.WARNING,
        last_check=1000, last_state_change=900, plugin_output="WARNING - high load", polled_at=1000,
    )
    view = HostView(
        host_name="HOST1", current_state=HostState.UP, downtime_seconds=0,
        downtime_human="-", plugin_output="", last_check=1000, polled_at=1000,
        pattern=Pattern.STABLE, match_type=MatchType.NO_MATCH, entry=None,
        tags=[], services=[service],
    )
    monkeypatch.setattr(api, "get_current_hosts", lambda: [view])
    response = client.get("/api/hosts")
    body = response.json()
    assert body[0]["services"] == [{
        "service_description": "CPU Load", "current_state": "WARNING",
        "last_check": 1000, "last_state_change": 900, "plugin_output": "WARNING - high load",
    }]


def test_replace_host_updates_full_row(monkeypatch):
    calls = []

    def fake_replace(host_name, nombre_pc, rak, anydesk_id, crd_host_id=None, cliente=None, motivo=None):
        calls.append((host_name, nombre_pc, rak, anydesk_id, crd_host_id, cliente))
        return ReplaceResult(
            old_rak="RAKVIEJO456", old_host_name="RAKVIEJO456", new_host_name="RAKNUEVO123",
            nagios_applied=True, old_removed=True, validation_output="Total Errors: 0",
        )

    monkeypatch.setattr(api, "replace_host_inventory", fake_replace)
    response = client.post(
        "/api/hosts/HOST1/replace",
        json={"nombre_pc": "Nuevo Nombre", "rak": "RAKNUEVO123", "anydesk_id": "999",
              "motivo": "falla_pc"},
    )
    assert response.status_code == 200
    assert calls == [("HOST1", "Nuevo Nombre", "RAKNUEVO123", "999", None, None)]
    body = response.json()
    assert body["nagios_applied"] is True
    assert body["old_rak"] == "RAKVIEJO456"


def test_replace_host_returns_422_when_nagios_validation_fails(monkeypatch):
    def raise_validation_error(host_name, nombre_pc, rak, anydesk_id, crd_host_id=None, cliente=None, motivo=None):
        raise NagiosValidationError("no validó — se revirtió el inventario")

    monkeypatch.setattr(api, "replace_host_inventory", raise_validation_error)
    response = client.post(
        "/api/hosts/HOST1/replace",
        json={"nombre_pc": "N", "rak": "RAKNUEVO123", "anydesk_id": "999", "motivo": "falla_pc"},
    )
    assert response.status_code == 422
    assert "revirtió" in response.json()["detail"]


def test_register_host_creates_and_registers(monkeypatch):
    calls = []

    def fake_register(rak, nombre_pc="", cliente="", anydesk_id="", crd_host_id="", notas=""):
        calls.append((rak, nombre_pc, cliente))
        return HostRegistrationResult(
            host_name="RAKNUEVO123", remote_path="/hosts/RAKNUEVO123.cfg",
            inventory_row_created=True, validation_output="Total Errors: 0",
        )

    monkeypatch.setattr(api, "register_host", fake_register)
    response = client.post(
        "/api/nagios-uploader/register",
        json={"rak": "raknuevo123", "nombre_pc": "Sitio Nuevo", "cliente": "GCBA"},
    )
    assert response.status_code == 200
    assert calls == [("raknuevo123", "Sitio Nuevo", "GCBA")]
    assert response.json()["inventory_row_created"] is True


def test_register_host_returns_422_when_inventory_data_missing(monkeypatch):
    def raise_missing(rak, nombre_pc="", cliente="", anydesk_id="", crd_host_id="", notas=""):
        raise MissingInventoryDataError("hacen falta Nombre PC y Ambiente")

    monkeypatch.setattr(api, "register_host", raise_missing)
    response = client.post("/api/nagios-uploader/register", json={"rak": "raknuevo123"})
    assert response.status_code == 422


def test_replace_host_returns_404_when_unmatched(monkeypatch):
    def raise_not_editable(host_name, nombre_pc, rak, anydesk_id, crd_host_id=None, cliente=None, motivo=None):
        raise HostNotEditableError(f"'{host_name}' no tiene fila propia.")

    monkeypatch.setattr(api, "replace_host_inventory", raise_not_editable)
    response = client.post(
        "/api/hosts/HOST1/replace",
        json={"nombre_pc": "X", "rak": "Y", "anydesk_id": "Z", "motivo": "refurbish"},
    )
    assert response.status_code == 404


def test_replace_host_returns_503_when_inventory_locked(monkeypatch):
    def raise_write_error(host_name, nombre_pc, rak, anydesk_id, crd_host_id=None, cliente=None, motivo=None):
        raise InventoryWriteError("archivo abierto en otro programa")

    monkeypatch.setattr(api, "replace_host_inventory", raise_write_error)
    response = client.post(
        "/api/hosts/HOST1/replace",
        json={"nombre_pc": "X", "rak": "Y", "anydesk_id": "Z", "motivo": "refurbish"},
    )
    assert response.status_code == 503


def test_create_inventory_entry(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api, "create_inventory_entry",
        lambda **kwargs: calls.append(kwargs),
    )
    response = client.post("/api/inventory/entries", json={
        "nombre_pc": "Equipo Nuevo", "rak": "RAKNUEVO123", "cliente": "GCBA",
    })
    assert response.status_code == 201
    assert calls == [{
        "nombre_pc": "Equipo Nuevo", "rak": "RAKNUEVO123", "cliente": "GCBA",
        "anydesk_id": "", "crd_host_id": "", "notas": "",
    }]


def test_create_inventory_entry_rejects_empty_rak():
    response = client.post("/api/inventory/entries", json={
        "nombre_pc": "Equipo Nuevo", "rak": "   ", "cliente": "GCBA",
    })
    assert response.status_code == 422


def test_create_inventory_entry_returns_503_when_locked(monkeypatch):
    def raise_write_error(**kwargs):
        raise InventoryWriteError("archivo abierto en otro programa")

    monkeypatch.setattr(api, "create_inventory_entry", raise_write_error)
    response = client.post("/api/inventory/entries", json={
        "nombre_pc": "Equipo Nuevo", "rak": "RAKNUEVO123", "cliente": "GCBA",
    })
    assert response.status_code == 503


def test_prepare_nagios_add(monkeypatch):
    monkeypatch.setattr(
        api, "prepare_add_host",
        lambda rak: AddHostPlan(
            host_name="RAKNUEVO12345",
            filename="RAKNUEVO12345.cfg",
            remote_path="/usr/local/nagios/etc/objects/hosts/RAKNUEVO12345.cfg",
            content="define host {\nuse windows_powershell\nhost_name RAKNUEVO12345\n}\n",
        ),
    )
    response = client.post("/api/nagios-uploader/prepare-add", json={"rak": "raknuevo12345"})
    assert response.status_code == 200
    body = response.json()
    assert body["host_name"] == "RAKNUEVO12345"
    assert "host_name RAKNUEVO12345" in body["content"]


def test_prepare_nagios_add_rejects_invalid_rak(monkeypatch):
    def raise_invalid(rak):
        raise InvalidRakError("RAK inválido")

    monkeypatch.setattr(api, "prepare_add_host", raise_invalid)
    response = client.post("/api/nagios-uploader/prepare-add", json={"rak": "---"})
    assert response.status_code == 422


def test_prepare_nagios_remove(monkeypatch):
    monkeypatch.setattr(
        api, "prepare_remove_host",
        lambda host_name: RemoveHostPlan(
            host_name="HOST1", filename="HOST1.cfg",
            remote_path="/usr/local/nagios/etc/objects/hosts/HOST1.cfg",
            current_content="define host {\nuse windows_powershell\nhost_name HOST1\n}\n",
        ),
    )
    response = client.post("/api/nagios-uploader/prepare-remove", json={"host_name": "HOST1"})
    assert response.status_code == 200
    body = response.json()
    assert body["existed"] is True
    assert "HOST1" in body["current_content"]


def test_prepare_nagios_remove_returns_503_on_ssh_error(monkeypatch):
    def raise_ssh_error(host_name):
        raise NagiosSSHError("no se pudo conectar")

    monkeypatch.setattr(api, "prepare_remove_host", raise_ssh_error)
    response = client.post("/api/nagios-uploader/prepare-remove", json={"host_name": "HOST1"})
    assert response.status_code == 503


def test_execute_nagios_add(monkeypatch):
    monkeypatch.setattr(
        api, "execute_add_host",
        lambda rak: AddHostResult(
            host_name="RAKNUEVO12345", filename="RAKNUEVO12345.cfg",
            remote_path="/usr/local/nagios/etc/objects/hosts/RAKNUEVO12345.cfg",
            validation_output="Total Warnings: 0\nTotal Errors: 0\n",
        ),
    )
    response = client.post("/api/nagios-uploader/execute-add", json={"rak": "raknuevo12345"})
    assert response.status_code == 200
    body = response.json()
    assert body["host_name"] == "RAKNUEVO12345"
    assert "Total Errors: 0" in body["validation_output"]


def test_execute_nagios_add_rejects_invalid_rak(monkeypatch):
    def raise_invalid(rak):
        raise InvalidRakError("RAK inválido")

    monkeypatch.setattr(api, "execute_add_host", raise_invalid)
    response = client.post("/api/nagios-uploader/execute-add", json={"rak": "---"})
    assert response.status_code == 422


def test_execute_nagios_add_returns_422_on_validation_failure(monkeypatch):
    def raise_validation_error(rak):
        raise NagiosValidationError("La config no validó — se revirtió el alta.")

    monkeypatch.setattr(api, "execute_add_host", raise_validation_error)
    response = client.post("/api/nagios-uploader/execute-add", json={"rak": "RAKNUEVO12345"})
    assert response.status_code == 422
    assert "revirtió" in response.json()["detail"]


def test_execute_nagios_add_returns_502_on_reload_failure(monkeypatch):
    def raise_reload_error(rak):
        raise NagiosReloadError("No se pudo recargar Nagios.")

    monkeypatch.setattr(api, "execute_add_host", raise_reload_error)
    response = client.post("/api/nagios-uploader/execute-add", json={"rak": "RAKNUEVO12345"})
    assert response.status_code == 502


def test_execute_nagios_add_returns_503_on_ssh_error(monkeypatch):
    def raise_ssh_error(rak):
        raise NagiosSSHError("no se pudo conectar")

    monkeypatch.setattr(api, "execute_add_host", raise_ssh_error)
    response = client.post("/api/nagios-uploader/execute-add", json={"rak": "RAKNUEVO12345"})
    assert response.status_code == 503


def test_execute_nagios_remove(monkeypatch):
    monkeypatch.setattr(
        api, "execute_remove_host",
        lambda host_name: RemoveHostResult(
            host_name="HOST1", filename="HOST1.cfg",
            remote_path="/usr/local/nagios/etc/objects/hosts/HOST1.cfg",
            validation_output="Total Warnings: 0\nTotal Errors: 0\n",
        ),
    )
    response = client.post("/api/nagios-uploader/execute-remove", json={"host_name": "HOST1"})
    assert response.status_code == 200
    assert response.json()["host_name"] == "HOST1"


def test_execute_nagios_remove_returns_404_when_host_not_found(monkeypatch):
    def raise_not_found(host_name):
        raise NagiosHostNotFoundError(f"'{host_name}' no tiene un .cfg en el servidor.")

    monkeypatch.setattr(api, "execute_remove_host", raise_not_found)
    response = client.post("/api/nagios-uploader/execute-remove", json={"host_name": "NOEXISTE"})
    assert response.status_code == 404


def test_execute_nagios_remove_returns_422_on_validation_failure(monkeypatch):
    def raise_validation_error(host_name):
        raise NagiosValidationError("La config no validó — se restauró el archivo.")

    monkeypatch.setattr(api, "execute_remove_host", raise_validation_error)
    response = client.post("/api/nagios-uploader/execute-remove", json={"host_name": "HOST1"})
    assert response.status_code == 422


def test_execute_nagios_remove_returns_503_on_ssh_error(monkeypatch):
    def raise_ssh_error(host_name):
        raise NagiosSSHError("no se pudo conectar")

    monkeypatch.setattr(api, "execute_remove_host", raise_ssh_error)
    response = client.post("/api/nagios-uploader/execute-remove", json={"host_name": "HOST1"})
    assert response.status_code == 503


def test_host_nagios_info(monkeypatch):
    monkeypatch.setattr(
        api, "get_host_nagios_info",
        lambda host_name: HostNagiosInfo(
            host_name=host_name, cfg_exists=True,
            cfg_modified_at=1750000000, first_seen_at=1755000000, ssh_error=None,
        ),
    )
    response = client.get("/api/hosts/HOST1/nagios-info")
    assert response.status_code == 200
    body = response.json()
    assert body["cfg_exists"] is True
    assert body["cfg_modified_at"] == 1750000000
    assert body["first_seen_at"] == 1755000000


def test_host_nagios_info_reports_ssh_error_without_failing(monkeypatch):
    """Si el servidor no responde, el detalle igual se sirve con el error como dato."""
    monkeypatch.setattr(
        api, "get_host_nagios_info",
        lambda host_name: HostNagiosInfo(
            host_name=host_name, cfg_exists=False, cfg_modified_at=None,
            first_seen_at=1755000000, ssh_error="No se pudo conectar",
        ),
    )
    response = client.get("/api/hosts/HOST1/nagios-info")
    assert response.status_code == 200
    assert response.json()["ssh_error"] == "No se pudo conectar"
    assert response.json()["first_seen_at"] == 1755000000


def test_download_html_report(monkeypatch):
    monkeypatch.setattr(api, "build_html_report", lambda: "<html><body>Informe de prueba</body></html>")
    response = client.get("/api/reports/html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" in response.headers["content-disposition"]
    assert "Informe de prueba" in response.text


def test_download_html_report_returns_503_when_inventory_unavailable(monkeypatch):
    def raise_error():
        raise InventoryUnavailableError("archivo no encontrado")

    monkeypatch.setattr(api, "build_html_report", raise_error)
    response = client.get("/api/reports/html")
    assert response.status_code == 503


def test_inventory_pending_serializes_hosts(monkeypatch):
    pending = [
        PendingInventoryHost(
            host_name="STOCKRAK0000000", nombre_pc="Equipo En Stock", ambiente="STOCK", rak="STOCKRAK000000001",
            tags=[Tag(id=1, name="RMA", color="#888888", category="rma")], estado="RMA",
        ),
    ]
    monkeypatch.setattr(api, "get_pending_inventory_hosts", lambda: pending)
    response = client.get("/api/inventory/pending")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["host_name"] == "STOCKRAK0000000"
    assert body[0]["tags"] == [{"id": 1, "name": "RMA", "color": "#888888", "category": "rma"}]
    # La situación viaja para poder cambiarla desde STOCK sin tocar los tags.
    assert body[0]["estado"] == "RMA"


def test_inventory_pending_returns_503_when_inventory_unavailable(monkeypatch):
    def raise_error():
        raise InventoryUnavailableError("archivo no encontrado")

    monkeypatch.setattr(api, "get_pending_inventory_hosts", raise_error)
    response = client.get("/api/inventory/pending")
    assert response.status_code == 503


def test_insights_endpoint(monkeypatch):
    monkeypatch.setattr(
        api, "get_insights",
        lambda: DashboardInsights(
            host_up=210, host_down=7, host_unreachable=0,
            service_ok=1242, service_warning=23, service_critical=7, service_unknown=72,
        ),
    )
    response = client.get("/api/insights")
    assert response.status_code == 200
    assert response.json() == {
        "host_up": 210, "host_down": 7, "host_unreachable": 0,
        "service_ok": 1242, "service_warning": 23, "service_critical": 7, "service_unknown": 72,
    }
