"""Lectura del inventario de AnyDesk desde un CSV plano (una fila por equipo).

Reemplaza al Excel multi-hoja de las Fases 2-6: un solo archivo CSV es
más portable para pasar la herramienta a otra persona/máquina (no
depende de OneDrive ni de una instalación de Excel), y la columna
Cliente ya era la fuente de verdad del ambiente — así que las "hojas"
del Excel nunca aportaron información que no estuviera ya en esa
columna.
"""

from __future__ import annotations

import csv
import logging
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

_EXPECTED_COLUMNS = ["Nombre PC", "RAK", "ID AnyDesk", "Cliente"]
_OPTIONAL_COLUMNS = ["ID Chrome Remote Desktop", "Notas", "SIM", "Empresa", "Estado"]
_ALL_COLUMNS = _EXPECTED_COLUMNS + _OPTIONAL_COLUMNS


class EstadoOperativo(str, Enum):
    """Dónde está parado un equipo en su ciclo de vida.

    Reemplaza al mecanismo anterior, en el que un tag de categoría especial
    (RMA/Testing/Refurbish) decidía si el equipo aparecía en Equipos o en
    STOCK. Ese modelo no tenía paso de graduación: cuando un equipo salía
    del refurbish nadie le quitaba el tag, y como el tag lo escondía de
    Equipos tampoco había dónde ir a quitárselo. Quedaba encerrado.

    Ahora el estado es un dato propio del equipo, editable siempre, y los
    tags vuelven a ser lo que debían: etiquetas libres de organización.
    """
    PRODUCCION = "Produccion"
    TESTING = "Testing"
    RMA = "RMA"
    REFURBISH = "Refurbish"
    BAJA = "Baja"

    @property
    def etiqueta(self) -> str:
        return {
            EstadoOperativo.PRODUCCION: "Producción",
            EstadoOperativo.TESTING: "Testing",
            EstadoOperativo.RMA: "RMA",
            EstadoOperativo.REFURBISH: "Refurbish",
            EstadoOperativo.BAJA: "Baja",
        }[self]

    @property
    def es_productivo(self) -> bool:
        """Solo Producción se monitorea como equipo en servicio."""
        return self is EstadoOperativo.PRODUCCION


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def parse_estado(valor: str | None) -> EstadoOperativo:
    """Interpreta la columna Estado del CSV.

    Tolerante a propósito: la planilla se edita a mano y aparecen
    "producción", "PRODUCCION", "En producción" o la celda vacía. Vacío
    significa Producción, porque las 408 filas que ya existían son equipos
    en servicio y no tiene sentido obligar a completarlas una por una.
    """
    if not valor or not valor.strip():
        return EstadoOperativo.PRODUCCION
    normalizado = _sin_acentos(valor.strip()).upper()
    for estado in EstadoOperativo:
        if estado.value.upper() in normalizado:
            return estado
    logger.warning("Estado no reconocido en el inventario: %r — se asume Producción.", valor)
    return EstadoOperativo.PRODUCCION


@dataclass(frozen=True)
class InventoryEntry:
    nombre_pc: str
    rak: str
    anydesk_id: str
    cliente: str
    row_number: int
    crd_host_id: str = ""
    notas: str = ""
    sim: str = ""
    empresa: str = ""
    estado: EstadoOperativo = EstadoOperativo.PRODUCCION

    @property
    def is_stock(self) -> bool:
        return self.cliente.strip().upper() == "STOCK"


def load_inventory(csv_path: str) -> list[InventoryEntry]:
    """Carga el CSV de inventario. Las columnas se ubican por encabezado, no por posición.

    Si falta una columna esperada, se loguea un warning claro y esa
    columna queda vacía para todas las filas, en vez de romper la carga
    completa del archivo.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"No se encontró el inventario en INVENTORY_CSV_PATH: {csv_path}")

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            logger.warning("El CSV de inventario está vacío.")
            return []

        column_index = _resolve_columns(header)
        if column_index is None:
            return []

        entries = []
        for row_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            entry = _build_entry(row_number, row, column_index)
            if entry is not None:
                entries.append(entry)

    logger.info("Inventario cargado: %d entradas.", len(entries))
    return entries


def _resolve_columns(header: list[str]) -> dict[str, int] | None:
    normalized_header = [cell.strip() for cell in header]
    column_index: dict[str, int] = {}
    missing = []

    for expected in _EXPECTED_COLUMNS:
        try:
            column_index[expected] = normalized_header.index(expected)
        except ValueError:
            missing.append(expected)

    if missing:
        logger.warning(
            "Faltan columnas esperadas %s en el inventario (encabezado encontrado: %s). "
            "Esos campos quedan vacíos.",
            missing, normalized_header,
        )

    for optional in _OPTIONAL_COLUMNS:
        try:
            column_index[optional] = normalized_header.index(optional)
        except ValueError:
            pass  # columna opcional: si no está, el campo queda vacío sin warning

    if not column_index:
        logger.warning("No se reconoció ninguna columna esperada en el inventario.")
        return None

    return column_index


def _build_entry(row_number: int, row: list[str], column_index: dict[str, int]) -> InventoryEntry | None:
    def cell(name: str) -> str:
        idx = column_index.get(name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    return InventoryEntry(
        nombre_pc=cell("Nombre PC"),
        rak=cell("RAK"),
        anydesk_id=cell("ID AnyDesk"),
        cliente=cell("Cliente"),
        row_number=row_number,
        crd_host_id=cell("ID Chrome Remote Desktop"),
        notas=cell("Notas"),
        sim=cell("SIM"),
        empresa=cell("Empresa"),
        estado=parse_estado(cell("Estado")),
    )
