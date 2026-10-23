"""Cruza el inventario de SIMs de Movistar (Excel aparte) contra el inventario
principal y completa las columnas SIM/Empresa para los equipos que ya están
dados de alta en Nagios.

Solo se usa el RAK del Excel de Movistar para cruzar — el nombre de CSL que
trae esa planilla se ignora a propósito (la fuente de verdad de nombres es
el inventario principal). El host_name se deriva del RAK igual que en Nagios
Uploader (normalizado y truncado a 15), y solo se completa la SIM si ese
host_name ya reportó a Nagios alguna vez Y el inventario tiene una fila con
ambiente para ese RAK — así no se pisa nada de equipos que todavía no están
en producción.

Uso:
    python match_movistar_sims.py <ruta_excel_movistar>              # dry-run, solo imprime
    python match_movistar_sims.py <ruta_excel_movistar> --apply      # escribe SIM/Empresa de verdad
"""

from __future__ import annotations

import argparse
import logging
import sys

import openpyxl

from app.config import load_database_path, load_inventory_csv_path
from app.db import get_all_host_names, get_connection
from app.inventory_loader import load_inventory
from app.inventory_writer import InventoryWriteError, update_inventory_row
from app.matching import match_host
from app.nagios_uploader import InvalidRakError, derive_host_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("match_movistar_sims")

EMPRESA = "Movistar"


def read_movistar_excel(path: str) -> list[tuple[str, str]]:
    """Devuelve [(sim, rak), ...] desde la hoja única del Excel de Movistar."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    header = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
    sim_idx = header.index("SIM")
    rak_idx = header.index("RAK")

    pairs = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        sim = str(row[sim_idx]).strip() if row[sim_idx] is not None else ""
        rak = str(row[rak_idx]).strip() if row[rak_idx] is not None else ""
        if sim and rak:
            pairs.append((sim, rak))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excel_path", help="Ruta al Excel 'Inventario de CSL Movistar'")
    parser.add_argument("--apply", action="store_true", help="Escribir de verdad (si no, solo imprime el cruce)")
    args = parser.parse_args()

    pairs = read_movistar_excel(args.excel_path)
    logger.info("%d fila(s) leídas del Excel de Movistar.", len(pairs))

    conn = get_connection(load_database_path())
    try:
        known_host_names = get_all_host_names(conn)
    finally:
        conn.close()

    csv_path = load_inventory_csv_path()
    inventory = load_inventory(csv_path)

    # Detectar RAKs duplicados en el Excel ANTES de cruzar nada — así un
    # conflicto se detecta siempre, no solo cuando la primera aparición del
    # RAK llega a matchear.
    rak_counts: dict[str, int] = {}
    for _, rak in pairs:
        rak_counts[rak] = rak_counts.get(rak, 0) + 1
    duplicated_raks = {rak for rak, count in rak_counts.items() if count > 1}

    matched: list[tuple[str, str, int, str]] = []  # (sim, rak, row_number, nombre_pc)
    skipped_conflict: list[tuple[str, str]] = []
    skipped_not_in_nagios: list[tuple[str, str]] = []
    skipped_no_ambiente: list[tuple[str, str]] = []
    skipped_invalid: list[tuple[str, str]] = []

    for sim, rak in pairs:
        if rak in duplicated_raks:
            skipped_conflict.append((sim, rak))
            continue

        try:
            candidate_host_name = derive_host_name(rak)
        except InvalidRakError:
            skipped_invalid.append((sim, rak))
            continue

        # Comparación por igualdad (no por substring): candidate_host_name ya
        # viene normalizado/truncado igual que lo reportaría el agente de
        # Nagios, así que si el host existe va a coincidir exacto.
        if candidate_host_name not in known_host_names:
            skipped_not_in_nagios.append((sim, rak))
            continue

        match = match_host(candidate_host_name, inventory)
        if match.entry is None or not match.entry.cliente.strip():
            skipped_no_ambiente.append((sim, rak))
            continue

        matched.append((sim, rak, match.entry.row_number, match.entry.nombre_pc))

    print(f"\nTotal filas en el Excel: {len(pairs)}")
    print(f"Matcheadas (en Nagios + con ambiente): {len(matched)}")
    print(f"Sin actividad en Nagios todavía: {len(skipped_not_in_nagios)}")
    print(f"Sin ambiente en el inventario: {len(skipped_no_ambiente)}")
    print(f"RAK duplicado en el Excel (ambiguo, se omite): {len(skipped_conflict)}")
    print(f"RAK inválido: {len(skipped_invalid)}")

    if matched:
        print("\n--- Matcheados ---")
        for sim, rak, row_number, nombre_pc in matched:
            print(f"  fila{row_number} — {nombre_pc or 'sin identificar'} — RAK {rak} — SIM {sim}")

    if skipped_conflict:
        print("\n--- Conflicto: mismo RAK aparece más de una vez en el Excel con distinta SIM (revisar a mano) ---")
        for sim, rak in skipped_conflict:
            print(f"  RAK {rak} — SIM: {sim}")

    if not args.apply:
        print("\n(dry-run — no se escribió nada. Correr con --apply para aplicar los cambios.)")
        return 0

    for sim, rak, row_number, nombre_pc in matched:
        try:
            update_inventory_row(csv_path, row_number, sim=sim, empresa=EMPRESA)
        except InventoryWriteError as exc:
            logger.error("No se pudo escribir fila %d (%s): %s", row_number, nombre_pc, exc)
            return 1

    logger.info("Aplicado: %d fila(s) actualizadas con SIM/Empresa.", len(matched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
