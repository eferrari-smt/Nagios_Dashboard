"""Escritura al CSV de inventario.

Dos niveles de escritura, a propósito bien distintos:

- `update_inventory_row`: edición acotada (Nombre PC / Notas / SIM / Empresa /
  Ambiente) desde el botón "Editar" de cada fila. Nunca toca el RAK.
- `replace_inventory_row` / `append_inventory_row`: reemplazo completo
  (Nombre, RAK, ID AnyDesk, ID CRD) desde la sección "Reemplazar" —
  la única vía deliberada para tocar el RAK, para el caso de un CSL
  físico reemplazado en la calle o un equipo nuevo que hay que dar de
  alta. Es una acción separada y explícita, no una casilla más del
  editor común.
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.matching import normalize

_EXPECTED_COLUMNS = ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"]
_OPTIONAL_COLUMNS = ["ID Chrome Remote Desktop", "Notas"]


class InventoryWriteError(RuntimeError):
    """No se pudo escribir en el CSV (ej. abierto en otro programa)."""


def _read_rows(csv_path: str) -> list[list[str]]:
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))
    except PermissionError as exc:
        raise InventoryWriteError(
            "No se pudo abrir el inventario para escribir: probablemente esté abierto en otro "
            "programa (Excel/editor). Cerralo e intentá de nuevo."
        ) from exc
    except FileNotFoundError as exc:
        raise InventoryWriteError(f"No se encontró el inventario en {csv_path}.") from exc


def _write_rows(csv_path: str, rows: list[list[str]]) -> None:
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
    except PermissionError as exc:
        raise InventoryWriteError(
            "No se pudo guardar el inventario: probablemente esté abierto en otro programa. "
            "Cerralo e intentá de nuevo."
        ) from exc


def _column_index(header: list[str], name: str) -> int:
    if name in header:
        return header.index(name)
    header.append(name)
    return len(header) - 1


def _ensure_row_length(rows: list[list[str]], length: int) -> None:
    for row in rows:
        if len(row) < length:
            row.extend([""] * (length - len(row)))


def update_inventory_row(
    csv_path: str,
    row_number: int,
    *,
    nombre_pc: str | None = None,
    notas: str | None = None,
    sim: str | None = None,
    empresa: str | None = None,
    cliente: str | None = None,
    estado: str | None = None,
) -> None:
    rows = _read_rows(csv_path)
    if not rows:
        raise InventoryWriteError("El inventario está vacío.")

    header = rows[0]
    idx = row_number - 1
    if idx < 1 or idx >= len(rows):
        raise InventoryWriteError(f"La fila {row_number} ya no existe en el inventario.")

    fields = {
        "Nombre PC": nombre_pc, "Notas": notas, "SIM": sim,
        "Empresa": empresa, "Cliente": cliente, "Estado": estado,
    }
    for column, value in fields.items():
        if value is None:
            continue
        col = _column_index(header, column)
        _ensure_row_length(rows, len(header))
        rows[idx][col] = value

    _write_rows(csv_path, rows)


def replace_inventory_row(
    csv_path: str,
    row_number: int,
    *,
    nombre_pc: str,
    rak: str,
    anydesk_id: str,
    cliente: str | None = None,
    crd_host_id: str | None = None,
    notas: str | None = None,
) -> None:
    """Reemplazo completo de una fila existente — la única vía que toca el RAK."""
    rows = _read_rows(csv_path)
    if not rows:
        raise InventoryWriteError("El inventario está vacío.")

    header = rows[0]
    idx = row_number - 1
    if idx < 1 or idx >= len(rows):
        raise InventoryWriteError(f"La fila {row_number} ya no existe en el inventario.")

    _ensure_row_length(rows, len(header))
    rows[idx][_column_index(header, "Nombre PC")] = nombre_pc
    rows[idx][_column_index(header, "RAK")] = rak
    rows[idx][_column_index(header, "ID AnyDesk")] = anydesk_id
    if cliente is not None:
        rows[idx][_column_index(header, "Cliente")] = cliente
    if crd_host_id is not None:
        _ensure_row_length(rows, len(header))
        rows[idx][_column_index(header, "ID Chrome Remote Desktop")] = crd_host_id
    if notas is not None:
        _ensure_row_length(rows, len(header))
        rows[idx][_column_index(header, "Notas")] = notas

    _ensure_row_length(rows, len(header))
    _write_rows(csv_path, rows)


def append_inventory_row(
    csv_path: str,
    *,
    nombre_pc: str,
    rak: str,
    cliente: str,
    anydesk_id: str = "",
    crd_host_id: str = "",
    notas: str = "",
) -> int:
    """Agrega un equipo nuevo al inventario (ej. resolver un host de Inconsistencias). Devuelve el row_number asignado."""
    rows = _read_rows(csv_path)
    if not rows:
        rows = [_EXPECTED_COLUMNS + _OPTIONAL_COLUMNS]

    header = rows[0]
    values = {
        "Nombre PC": nombre_pc,
        "RAK": rak,
        "Cliente": cliente,
        "ID AnyDesk": anydesk_id,
        "ID Chrome Remote Desktop": crd_host_id,
        "Notas": notas,
    }
    columns = {name: _column_index(header, name) for name in values}

    new_row = [""] * len(header)
    _ensure_row_length(rows, len(header))
    for name, value in values.items():
        new_row[columns[name]] = value
    rows.append(new_row)

    _write_rows(csv_path, rows)
    return len(rows)  # 1-based row number of the newly appended row


def delete_inventory_row(csv_path: str, row_number: int, *, expected_rak: str) -> str:
    """Borra una fila del inventario. Devuelve el "Nombre PC" de lo borrado.

    `expected_rak` no es opcional a propósito: los números de fila se corren
    cuando se borra algo, así que si dos pestañas tienen la vista vieja, la
    segunda borraría al vecino de la fila que quería borrar. Se compara el RAK
    antes de tocar nada y, si no coincide, se aborta pidiendo recargar.
    """
    rows = _read_rows(csv_path)
    if not rows:
        raise InventoryWriteError("El inventario está vacío.")

    header = rows[0]
    idx = row_number - 1
    if idx < 1 or idx >= len(rows):
        raise InventoryWriteError(f"La fila {row_number} ya no existe en el inventario.")

    col_rak = _column_index(header, "RAK")
    col_nombre = _column_index(header, "Nombre PC")
    _ensure_row_length(rows, len(header))

    rak_actual = rows[idx][col_rak].strip()
    if normalize(rak_actual) != normalize(expected_rak):
        raise InventoryWriteError(
            "El inventario cambió desde que cargaste la pantalla: la fila que ibas a borrar "
            f"ahora tiene el RAK '{rak_actual}'. Recargá y volvé a intentar."
        )

    nombre = rows[idx][col_nombre].strip()
    del rows[idx]
    _write_rows(csv_path, rows)
    return nombre
