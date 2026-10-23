"""Matching entre hosts de Nagios y el inventario de AnyDesk.

## El problema del truncado a 15 caracteres

Windows limita el nombre NetBIOS a 15 caracteres. El agente NRDP que
corre en cada CSL reporta ese nombre truncado, pero en el Excel los RAK
suelen estar completos (20+ caracteres), y en Nagios algunos hosts
fueron dados de alta directamente con el nombre largo. Un host Nagios
`MPC1BBAWT11MY260617` nunca va a ser igual al string que la PC realmente
reporta (`MPC1BBAWT11MY26`, sus primeros 15 caracteres). Por eso el
matching no puede ser una comparación de igualdad: hay que truncar el
lado de Nagios a 15 y buscarlo como substring dentro del RAK del
inventario, que sí viene completo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.inventory_loader import InventoryEntry

NAGIOS_NETBIOS_TRUNCATE_LEN = 15

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize(text: str) -> str:
    """Mayúsculas y solo alfanumérico. Los RAK del Excel traen prefijos/sufijos
    inconsistentes (guiones, espacios, texto de ubicación tipo `TGACSL-`),
    así que se limpia todo lo que no sea letra o número antes de comparar.
    """
    if not text:
        return ""
    return _NON_ALNUM.sub("", text.upper())


class MatchType(str, Enum):
    RAK = "rak"
    NOMBRE_PC = "nombre_pc"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class MatchResult:
    host_name: str
    match_type: MatchType
    entry: InventoryEntry | None
    candidate_count: int  # cuántas entradas de inventario matchearon antes de resolver duplicados


def match_host(host_name: str, inventory: list[InventoryEntry]) -> MatchResult:
    """Busca la entrada de inventario correspondiente a un host de Nagios.

    Orden: (1) substring del nombre truncado dentro del RAK normalizado,
    (2) si no hay candidatos, mismo substring dentro de "Nombre PC"
    normalizado (cubre equipos de STOCK con RAK vacío y el RAK cargado
    como nombre). Ante múltiples candidatos se prioriza el que no sea
    STOCK, porque el equipo activo es el que importa para operar.
    """
    fragment = normalize(host_name)[:NAGIOS_NETBIOS_TRUNCATE_LEN]
    if not fragment:
        return MatchResult(host_name, MatchType.NO_MATCH, None, 0)

    rak_candidates = [
        entry for entry in inventory
        if entry.rak and fragment in normalize(entry.rak)
    ]
    if rak_candidates:
        return MatchResult(
            host_name, MatchType.RAK, _resolve_duplicate(rak_candidates), len(rak_candidates)
        )

    nombre_candidates = [
        entry for entry in inventory
        if entry.nombre_pc and fragment in normalize(entry.nombre_pc)
    ]
    if nombre_candidates:
        return MatchResult(
            host_name, MatchType.NOMBRE_PC, _resolve_duplicate(nombre_candidates), len(nombre_candidates)
        )

    return MatchResult(host_name, MatchType.NO_MATCH, None, 0)


def _resolve_duplicate(candidates: list[InventoryEntry]) -> InventoryEntry:
    non_stock = [c for c in candidates if not c.is_stock]
    return non_stock[0] if non_stock else candidates[0]


@dataclass(frozen=True)
class DuplicateGroup:
    rak_normalized: str
    anydesk_id: str
    entries: list[InventoryEntry]


def find_inventory_duplicates(inventory: list[InventoryEntry]) -> list[DuplicateGroup]:
    """Agrupa entradas del inventario que comparten RAK e ID de AnyDesk.

    Son inconsistencias conocidas de la planilla (mismo equipo cargado en
    dos hojas). No se corrigen automáticamente, solo se reportan para que
    alguien limpie la planilla con criterio.
    """
    groups: dict[tuple[str, str], list[InventoryEntry]] = {}
    for entry in inventory:
        normalized_rak = normalize(entry.rak)
        if not normalized_rak or not entry.anydesk_id:
            continue
        key = (normalized_rak, entry.anydesk_id.strip())
        groups.setdefault(key, []).append(entry)

    return [
        DuplicateGroup(rak_normalized=key[0], anydesk_id=key[1], entries=entries)
        for key, entries in groups.items()
        if len(entries) > 1
    ]
