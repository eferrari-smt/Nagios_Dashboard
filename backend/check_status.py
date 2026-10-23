"""Fase 1: conecta a la EC2 de Nagios, lee status.dat e imprime el estado de cada host.

Uso:
    python check_status.py [--only-down]
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import load_nagios_ssh_config
from app.ssh_client import NagiosSSHError, fetch_status_dat
from app.status_parser import HostState, parse_status_dat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("check_status")

_STATE_LABEL = {
    HostState.UP: "UP",
    HostState.DOWN: "DOWN",
    HostState.UNREACHABLE: "UNREACHABLE",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only-down",
        action="store_true",
        help="Mostrar solo hosts en DOWN o UNREACHABLE",
    )
    args = parser.parse_args()

    try:
        config = load_nagios_ssh_config()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    try:
        raw = fetch_status_dat(config)
    except NagiosSSHError as exc:
        logger.error("No se pudo obtener status.dat: %s", exc)
        return 1

    hosts = parse_status_dat(raw)
    if args.only_down:
        hosts = [h for h in hosts if h.current_state != HostState.UP]

    hosts.sort(key=lambda h: (h.current_state != HostState.UP, h.host_name), reverse=True)

    print(f"\n{len(hosts)} host(s) — {config.host}\n")
    header = f"{'HOST_NAME':<22} {'ESTADO':<12} {'DOWNTIME':<14} PLUGIN_OUTPUT"
    print(header)
    print("-" * len(header))
    for host in hosts:
        label = _STATE_LABEL.get(host.current_state, str(host.current_state))
        downtime = host.downtime_human() if host.current_state != HostState.UP else "-"
        output = host.plugin_output[:60]
        print(f"{host.host_name:<22} {label:<12} {downtime:<14} {output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
