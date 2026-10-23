import pytest

from app import dashboard_service
from app.dashboard_service import (
    HostNotEditableError,
    MissingInventoryDataError,
    edit_host_inventory,
    register_host,
    replace_host_inventory,
)
from app.inventory_loader import InventoryEntry
from app.inventory_writer import InventoryWriteError
from app.nagios_uploader import NagiosValidationError

MATCHED_ENTRY = InventoryEntry(
    nombre_pc="Av. Monroe 1231", rak="ABCDEF1234567890XYZ", anydesk_id="123456",
    cliente="GCBA", row_number=42,
)


def test_edit_host_inventory_calls_writer_with_matched_row(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: [MATCHED_ENTRY])
    monkeypatch.setattr(dashboard_service, "load_inventory_csv_path", lambda: "C:/fake/inv.csv")

    calls = []
    monkeypatch.setattr(
        dashboard_service, "update_inventory_row",
        lambda path, row, nombre_pc=None, notas=None, sim=None, empresa=None, cliente=None, estado=None: calls.append(
            (path, row, nombre_pc, notas, sim, empresa, cliente)
        ),
    )
    monkeypatch.setattr(
        dashboard_service, "match_host",
        lambda host_name, inventory: type("M", (), {"entry": MATCHED_ENTRY})(),
    )

    edit_host_inventory("SOME_HOST", nombre_pc="Nuevo Nombre", notas="Nota", ambiente="Oficina")

    assert calls == [("C:/fake/inv.csv", 42, "Nuevo Nombre", "Nota", None, None, "Oficina")]


def test_edit_host_inventory_raises_when_unmatched(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: [])
    monkeypatch.setattr(
        dashboard_service, "match_host",
        lambda host_name, inventory: type("M", (), {"entry": None})(),
    )

    with pytest.raises(HostNotEditableError):
        edit_host_inventory("SIN_MATCH", nombre_pc="X")


def test_edit_host_inventory_propagates_write_error(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: [MATCHED_ENTRY])
    monkeypatch.setattr(dashboard_service, "load_inventory_csv_path", lambda: "C:/fake/inv.csv")
    monkeypatch.setattr(
        dashboard_service, "match_host",
        lambda host_name, inventory: type("M", (), {"entry": MATCHED_ENTRY})(),
    )

    def raise_write_error(*args, **kwargs):
        raise InventoryWriteError("archivo bloqueado")

    monkeypatch.setattr(dashboard_service, "update_inventory_row", raise_write_error)

    with pytest.raises(InventoryWriteError):
        edit_host_inventory("SOME_HOST", nombre_pc="X")


def _stub_replace_environment(monkeypatch, entry=MATCHED_ENTRY):
    """Deja el entorno mockeado para probar replace_host_inventory sin tocar disco ni red."""
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: [entry])
    monkeypatch.setattr(dashboard_service, "load_inventory_csv_path", lambda: "C:/fake/inv.csv")
    monkeypatch.setattr(
        dashboard_service, "match_host",
        lambda host_name, inventory: type("M", (), {"entry": entry})(),
    )
    csv_calls = []
    monkeypatch.setattr(
        dashboard_service, "replace_inventory_row",
        lambda path, row, nombre_pc, rak, anydesk_id, crd_host_id=None, cliente=None, notas=None: csv_calls.append(
            {"path": path, "row": row, "nombre_pc": nombre_pc, "rak": rak,
             "anydesk_id": anydesk_id, "crd_host_id": crd_host_id, "cliente": cliente, "notas": notas}
        ),
    )
    # El registro del reemplazo va a la base; acá se intercepta para no tocarla.
    monkeypatch.setattr(dashboard_service, "load_database_path", lambda: ":memory:")
    monkeypatch.setattr(dashboard_service, "get_connection", lambda path: _FakeConn())
    return csv_calls


class _FakeConn:
    def close(self):
        pass


def _capture_replacements(monkeypatch):
    registros = []
    monkeypatch.setattr(
        dashboard_service, "record_host_replacement",
        lambda conn, **kwargs: registros.append(kwargs),
    )
    return registros


def test_replace_host_inventory_writes_csv_and_applies_to_nagios(monkeypatch):
    csv_calls = _stub_replace_environment(monkeypatch)
    _capture_replacements(monkeypatch)
    nagios_calls = []
    monkeypatch.setattr(
        dashboard_service, "execute_replace_host",
        lambda old_rak, new_rak: nagios_calls.append((old_rak, new_rak)) or type(
            "R", (), {"old_host_name": "ABCDEF123456789", "new_host_name": "NUEVORAK123",
                      "old_removed": True, "validation_output": "Total Errors: 0"}
        )(),
    )

    result = replace_host_inventory("SOME_HOST", nombre_pc="Nombre Nuevo", rak="NUEVORAK123", anydesk_id="999")

    assert len(csv_calls) == 1
    assert csv_calls[0]["rak"] == "NUEVORAK123"
    assert nagios_calls == [("ABCDEF1234567890XYZ", "NUEVORAK123")]
    assert result.nagios_applied is True
    assert result.old_rak == "ABCDEF1234567890XYZ"


def test_replace_host_inventory_records_replacement_in_db(monkeypatch):
    """El reemplazo queda registrado en la base, y las Notas no se tocan."""
    csv_calls = _stub_replace_environment(monkeypatch)
    registros = _capture_replacements(monkeypatch)
    monkeypatch.setattr(
        dashboard_service, "execute_replace_host",
        lambda old_rak, new_rak: type(
            "R", (), {"old_host_name": "ABCDEF123456789", "new_host_name": "NUEVORAK123",
                      "old_removed": True, "validation_output": ""}
        )(),
    )

    replace_host_inventory("SOME_HOST", nombre_pc="N", rak="NUEVORAK123", anydesk_id="999")

    assert len(registros) == 1
    assert registros[0]["old_rak"] == "ABCDEF1234567890XYZ"
    assert registros[0]["new_rak"] == "NUEVORAK123"
    assert registros[0]["old_host_name"] == "ABCDEF123456789"
    assert registros[0]["new_host_name"] == "NUEVORAK123"
    assert csv_calls[0]["notas"] is None  # las Notas quedan para el usuario


def test_replace_host_inventory_skips_nagios_when_rak_unchanged(monkeypatch):
    _stub_replace_environment(monkeypatch)
    registros = _capture_replacements(monkeypatch)
    monkeypatch.setattr(
        dashboard_service, "execute_replace_host",
        lambda old_rak, new_rak: pytest.fail("no debería tocar Nagios si el RAK no cambió"),
    )

    result = replace_host_inventory(
        "SOME_HOST", nombre_pc="Otro Nombre", rak="abcdef-1234567890xyz", anydesk_id="999"
    )

    assert result.nagios_applied is False
    assert registros == []  # cambiar el nombre no es un reemplazo de equipo


def test_replace_host_inventory_reverts_csv_when_nagios_fails(monkeypatch):
    csv_calls = _stub_replace_environment(monkeypatch)

    def raise_validation_error(old_rak, new_rak):
        raise NagiosValidationError("no validó")

    monkeypatch.setattr(dashboard_service, "execute_replace_host", raise_validation_error)

    with pytest.raises(NagiosValidationError):
        replace_host_inventory("SOME_HOST", nombre_pc="Nombre Nuevo", rak="NUEVORAK123", anydesk_id="999")

    assert len(csv_calls) == 2  # el cambio y su reversión
    assert csv_calls[1]["rak"] == MATCHED_ENTRY.rak
    assert csv_calls[1]["nombre_pc"] == MATCHED_ENTRY.nombre_pc
    assert csv_calls[1]["notas"] == MATCHED_ENTRY.notas


def _stub_register_environment(monkeypatch, inventory):
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: inventory)
    monkeypatch.setattr(dashboard_service, "load_inventory_csv_path", lambda: "C:/fake/inv.csv")
    appended = []
    monkeypatch.setattr(
        dashboard_service, "append_inventory_row",
        lambda path, **kwargs: appended.append(kwargs),
    )
    registered = []
    monkeypatch.setattr(
        dashboard_service, "execute_add_host",
        lambda rak: registered.append(rak) or type(
            "R", (), {"host_name": "NEWRAK123", "remote_path": "/hosts/NEWRAK123.cfg",
                      "validation_output": "Total Errors: 0"}
        )(),
    )
    return appended, registered


def test_register_host_creates_inventory_row_when_rak_is_new(monkeypatch):
    appended, registered = _stub_register_environment(monkeypatch, [MATCHED_ENTRY])

    result = register_host(rak="NEWRAK123", nombre_pc="Sitio Nuevo", cliente="GCBA")

    assert result.inventory_row_created is True
    assert appended[0]["rak"] == "NEWRAK123"
    assert appended[0]["cliente"] == "GCBA"
    assert registered == ["NEWRAK123"]


def test_register_host_skips_inventory_row_when_rak_already_exists(monkeypatch):
    appended, registered = _stub_register_environment(monkeypatch, [MATCHED_ENTRY])

    result = register_host(rak="abcdef-1234567890xyz")  # mismo RAK que MATCHED_ENTRY, distinto formato

    assert result.inventory_row_created is False
    assert appended == []  # no duplica la fila
    assert registered == ["abcdef-1234567890xyz"]  # pero sí lo da de alta en Nagios


def test_register_host_raises_when_new_rak_lacks_nombre_or_ambiente(monkeypatch):
    appended, registered = _stub_register_environment(monkeypatch, [MATCHED_ENTRY])

    with pytest.raises(MissingInventoryDataError):
        register_host(rak="NEWRAK123", nombre_pc="Sitio Nuevo")  # falta ambiente

    assert appended == []
    assert registered == []  # no toca Nagios si no puede cargar el inventario


def test_replace_host_inventory_raises_when_unmatched(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: [])
    monkeypatch.setattr(
        dashboard_service, "match_host",
        lambda host_name, inventory: type("M", (), {"entry": None})(),
    )

    with pytest.raises(HostNotEditableError):
        replace_host_inventory("SIN_MATCH", nombre_pc="X", rak="Y", anydesk_id="Z")


def test_replace_host_inventory_propagates_write_error(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_load_inventory_or_raise", lambda: [MATCHED_ENTRY])
    monkeypatch.setattr(dashboard_service, "load_inventory_csv_path", lambda: "C:/fake/inv.csv")
    monkeypatch.setattr(
        dashboard_service, "match_host",
        lambda host_name, inventory: type("M", (), {"entry": MATCHED_ENTRY})(),
    )

    def raise_write_error(*args, **kwargs):
        raise InventoryWriteError("archivo bloqueado")

    monkeypatch.setattr(dashboard_service, "replace_inventory_row", raise_write_error)

    with pytest.raises(InventoryWriteError):
        replace_host_inventory("SOME_HOST", nombre_pc="X", rak="Y", anydesk_id="Z")
