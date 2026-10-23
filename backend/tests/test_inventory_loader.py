import pytest

from app.inventory_loader import load_inventory


def make_csv(path, header, rows):
    lines = [",".join(header)]
    lines.extend(",".join(row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def test_loads_entries_with_row_numbers(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(
        path,
        ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"],
        [["Sitio A", "RAK111", "111", "GCBA"], ["Sitio B", "RAK222", "222", "Rosario"]],
    )

    entries = load_inventory(str(path))
    assert len(entries) == 2
    assert entries[0].nombre_pc == "Sitio A"
    assert entries[0].row_number == 2
    assert entries[1].row_number == 3


def test_columns_located_by_header_not_position(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(
        path,
        ["Cliente", "Nombre PC", "ID AnyDesk", "RAK"],
        [["GCBA", "Sitio A", "111", "RAK111"]],
    )

    entries = load_inventory(str(path))
    assert entries[0].nombre_pc == "Sitio A"
    assert entries[0].rak == "RAK111"
    assert entries[0].cliente == "GCBA"


def test_optional_columns_default_to_empty_when_absent(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Sitio A", "RAK111", "111", "GCBA"]])

    entries = load_inventory(str(path))
    assert entries[0].crd_host_id == ""
    assert entries[0].notas == ""


def test_optional_columns_read_when_present(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(
        path,
        ["Nombre PC", "RAK", "ID AnyDesk", "Cliente", "ID Chrome Remote Desktop", "Notas"],
        [["Sitio A", "RAK111", "111", "GCBA", "999", "Revisar"]],
    )

    entries = load_inventory(str(path))
    assert entries[0].crd_host_id == "999"
    assert entries[0].notas == "Revisar"


def test_blank_rows_are_skipped(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(
        path,
        ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"],
        [["Sitio A", "RAK111", "111", "GCBA"], ["", "", "", ""], ["Sitio B", "RAK222", "222", "Rosario"]],
    )

    entries = load_inventory(str(path))
    assert len(entries) == 2


def test_missing_expected_column_defaults_to_empty_instead_of_failing(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "ID AnyDesk", "Cliente"], [["Sitio A", "111", "GCBA"]])

    entries = load_inventory(str(path))
    assert len(entries) == 1
    assert entries[0].rak == ""


def test_is_stock_property(tmp_path):
    path = tmp_path / "inv.csv"
    make_csv(path, ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"], [["Sitio A", "RAK111", "111", "stock"]])

    entries = load_inventory(str(path))
    assert entries[0].is_stock is True


def test_missing_file_raises_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_inventory(str(tmp_path / "no_existe.csv"))


def test_empty_file_returns_no_entries(tmp_path):
    path = tmp_path / "inv.csv"
    path.write_text("", encoding="utf-8-sig")

    assert load_inventory(str(path)) == []
