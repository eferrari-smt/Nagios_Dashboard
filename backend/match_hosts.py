"""Fase 2: cruza los hosts de Nagios contra el inventario de AnyDesk.

Uso:
    python match_hosts.py [--only-down] [--only-unmatched]
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import load_inventory_csv_path, load_nagios_ssh_config
from app.inventory_loader import load_inventory
from app.matching import MatchType, find_inventory_duplicates, match_host
from app.ssh_client import NagiosSSHError, fetch_status_dat
from app.status_parser import HostState, parse_status_dat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("match_hosts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-down", action="store_true", help="Solo hosts DOWN/UNREACHABLE")
    parser.add_argument("--only-unmatched", action="store_true", help="Solo hosts sin match en el inventario")
    args = parser.parse_args()

    try:
        ssh_config = load_nagios_ssh_config()
        inventory_path = load_inventory_csv_path()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    try:
        raw = fetch_status_dat(ssh_config)
    except NagiosSSHError as exc:
        logger.error("No se pudo obtener status.dat: %s", exc)
        return 1

    hosts = parse_status_dat(raw)
    if args.only_down:
        hosts = [h for h in hosts if h.current_state != HostState.UP]

    try:
        inventory = load_inventory(inventory_path)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return 1

    results = [match_host(h.host_name, inventory) for h in hosts]
    hosts_by_name = {h.host_name: h for h in hosts}

    if args.only_unmatched:
        results = [r for r in results if r.match_type == MatchType.NO_MATCH]

    matched = [r for r in results if r.match_type != MatchType.NO_MATCH]
    unmatched = [r for r in results if r.match_type == MatchType.NO_MATCH]

    print(f"\n{len(hosts)} host(s) Nagios — {len(inventory)} entrada(s) de inventario")
    print(f"Matches: {len(matched)}  |  Sin match: {len(unmatched)}\n")

    header = f"{'HOST_NAME':<20} {'ESTADO':<12} {'NOMBRE_PC':<28} {'AMBIENTE':<10} {'ANYDESK_ID':<12} MATCH"
    print(header)
    print("-" * len(header))
    for result in results:
        host = hosts_by_name[result.host_name]
        estado = host.current_state.name
        if result.entry is not None:
            nombre_pc = result.entry.nombre_pc[:28]
            ambiente = result.entry.cliente
            anydesk_id = result.entry.anydesk_id
        else:
            nombre_pc, ambiente, anydesk_id = "-", "-", "-"
        match_label = result.match_type.value + (
            f" ({result.candidate_count} cand.)" if result.candidate_count > 1 else ""
        )
        print(f"{result.host_name:<20} {estado:<12} {nombre_pc:<28} {ambiente:<10} {anydesk_id:<12} {match_label}")

    duplicates = find_inventory_duplicates(inventory)
    if duplicates:
        print(f"\n--- {len(duplicates)} grupo(s) de RAK duplicado en el inventario ---")
        for group in duplicates:
            locations = ", ".join(f"fila{e.row_number} ({e.cliente})" for e in group.entries)
            print(f"RAK~{group.rak_normalized[:20]} / AnyDesk {group.anydesk_id}: {locations}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
