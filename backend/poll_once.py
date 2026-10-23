"""Fase 3: guarda un snapshot en SQLite y muestra la clasificación de patrón de cada host.

Cada corrida representa un ciclo del poller. Para ver clasificaciones
como "intermitente" o "recuperado" hace falta correr este script varias
veces a lo largo del tiempo para acumular historial real.

Uso:
    python poll_once.py [--only-down]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from app.config import load_database_path, load_nagios_ssh_config
from app.db import get_connection, get_host_history, save_snapshot
from app.pattern_classifier import classify
from app.ssh_client import NagiosSSHError, fetch_status_dat
from app.status_parser import HostState, parse_status_dat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("poll_once")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-down", action="store_true", help="Solo hosts DOWN/UNREACHABLE")
    args = parser.parse_args()

    try:
        ssh_config = load_nagios_ssh_config()
        db_path = load_database_path()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    try:
        raw = fetch_status_dat(ssh_config)
    except NagiosSSHError as exc:
        logger.error("No se pudo obtener status.dat: %s", exc)
        return 1

    hosts = parse_status_dat(raw)
    polled_at = int(time.time())

    conn = get_connection(db_path)
    try:
        save_snapshot(conn, hosts, polled_at)

        display_hosts = hosts
        if args.only_down:
            display_hosts = [h for h in hosts if h.current_state != HostState.UP]

        print(f"\nSnapshot guardado ({len(hosts)} host(s), polled_at={polled_at}) en {db_path}\n")
        header = f"{'HOST_NAME':<22} {'ESTADO':<12} {'DOWNTIME':<14} {'PATRON':<14} SNAPSHOTS"
        print(header)
        print("-" * len(header))
        for host in display_hosts:
            history = get_host_history(conn, host.host_name)
            pattern = classify(history, now=polled_at)
            downtime = host.downtime_human(now=polled_at) if host.current_state != HostState.UP else "-"
            print(
                f"{host.host_name:<22} {host.current_state.name:<12} {downtime:<14} "
                f"{pattern.value:<14} {len(history)}"
            )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
