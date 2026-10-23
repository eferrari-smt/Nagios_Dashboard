import pytest

from app.inventory_loader import load_inventory
from app.inventory_writer import (
    InventoryWriteError,
    append_inventory_row,
    replace_inventory_row,
    update_inventory_row,
)


def make_csv(path, header, rows):
    lines = [",".join(header)]
    lines.extend(",".join(row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def test_update_nombre_pc(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Viejo Nombre", "RAK123", "111", "GCBA"]])

    update_inventory_row(str(path), 2, nombre_pc="Nombre Nuevo")

    entries = load_inventory(str(path))
    assert entries[0].nombre_pc == "Nombre Nuevo"
    assert entries[0].rak == "RAK123"  # intacto


def test_update_notas_creates_column_if_missing(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    update_inventory_row(str(path), 2, notas="Revisar cableado")

    entries = load_inventory(str(path))
    assert entries[0].notas == "Revisar cableado"
    assert entries[0].nombre_pc == "Nombre"  # intacto


def test_update_notas_reuses_existing_column(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(
        path,
        ["Nombre PC", "RAK", "ID AnyDesk", "Cliente", "Notas"],
        [["Nombre", "RAK123", "111", "GCBA", "nota vieja"]],
    )

    update_inventory_row(str(path), 2, notas="nota nueva")

    entries = load_inventory(str(path))
    assert entries[0].notas == "nota nueva"
    header_line = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header_line.count("Notas") == 1  # no duplicó la columna


def test_update_both_fields_at_once(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    update_inventory_row(str(path), 2, nombre_pc="Nombre Nuevo", notas="Nota")

    entries = load_inventory(str(path))
    assert entries[0].nombre_pc == "Nombre Nuevo"
    assert entries[0].notas == "Nota"


def test_update_sim_and_empresa_creates_columns_if_missing(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    update_inventory_row(str(path), 2, sim="8954000000000000002", empresa="Movistar")

    entries = load_inventory(str(path))
    assert entries[0].sim == "8954000000000000002"
    assert entries[0].empresa == "Movistar"
    assert entries[0].nombre_pc == "Nombre"  # intacto


def test_update_sim_reuses_existing_column(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(
        path,
        ["Nombre PC", "RAK", "ID AnyDesk", "Cliente", "SIM", "Empresa"],
        [["Nombre", "RAK123", "111", "GCBA", "sim vieja", "Personal"]],
    )

    update_inventory_row(str(path), 2, sim="sim nueva", empresa="Movistar")

    entries = load_inventory(str(path))
    assert entries[0].sim == "sim nueva"
    assert entries[0].empresa == "Movistar"
    header_line = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header_line.count("SIM") == 1  # no duplicó la columna


def test_update_cliente_changes_ambiente_without_touching_rak(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    update_inventory_row(str(path), 2, cliente="Oficina")

    entries = load_inventory(str(path))
    assert entries[0].cliente == "Oficina"
    assert entries[0].rak == "RAK123"  # el RAK nunca se toca desde acá


def test_update_never_touches_rak_column_even_if_untouched(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    update_inventory_row(str(path), 2, nombre_pc="Otro Nombre")

    entries = load_inventory(str(path))
    assert entries[0].rak == "RAK123"


def test_raises_when_row_missing(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    with pytest.raises(InventoryWriteError):
        update_inventory_row(str(path), 99, nombre_pc="X")


def test_raises_clear_error_when_file_locked(tmp_path, monkeypatch):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    def raise_permission_error(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr("builtins.open", raise_permission_error)

    with pytest.raises(InventoryWriteError):
        update_inventory_row(str(path), 2, nombre_pc="X")


def test_replace_inventory_row_can_change_rak(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    replace_inventory_row(str(path), 2, nombre_pc="Nombre Nuevo", rak="RAKNUEVO999", anydesk_id="222")

    entries = load_inventory(str(path))
    assert entries[0].nombre_pc == "Nombre Nuevo"
    assert entries[0].rak == "RAKNUEVO999"
    assert entries[0].anydesk_id == "222"


def test_replace_inventory_row_raises_when_row_missing(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    with pytest.raises(InventoryWriteError):
        replace_inventory_row(str(path), 99, nombre_pc="X", rak="Y", anydesk_id="Z")


def test_append_inventory_row_adds_new_entry(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Nombre", "RAK123", "111", "GCBA"]])

    row_number = append_inventory_row(str(path), nombre_pc="Nuevo Equipo", rak="RAKNUEVO000", cliente="Rosario")

    entries = load_inventory(str(path))
    assert len(entries) == 2
    assert entries[1].nombre_pc == "Nuevo Equipo"
    assert entries[1].rak == "RAKNUEVO000"
    assert entries[1].cliente == "Rosario"
    assert entries[1].row_number == row_number
