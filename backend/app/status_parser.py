"""Parser de bloques `hoststatus` y `servicestatus` del archivo status.dat de Nagios."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import IntEnum

logger = logging.getLogger(__name__)

_HOST_FIELDS = {
    "host_name",
    "current_state",
    "last_check",
    "last_state_change",
    "plugin_output",
    "last_time_up",
    "last_time_down",
}

_SERVICE_FIELDS = {
    "host_name",
    "service_description",
    "current_state",
    "last_check",
    "last_state_change",
    "plugin_output",
}


class HostState(IntEnum):
    UP = 0
    DOWN = 1
    UNREACHABLE = 2


class ServiceState(IntEnum):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    UNKNOWN = 3


@dataclass(frozen=True)
class HostStatus:
    host_name: str
    current_state: HostState
    last_check: int
    last_state_change: int
    plugin_output: str
    last_time_up: int
    last_time_down: int

    def downtime_seconds(self, now: int | None = None) -> int:
        """Segundos desde el último cambio de estado, si no está UP.

        Devuelve 0 cuando el host está UP: no tiene sentido hablar de
        downtime en ese caso.
        """
        if self.current_state == HostState.UP:
            return 0
        reference_now = now if now is not None else int(time.time())
        return max(0, reference_now - self.last_state_change)

    def downtime_human(self, now: int | None = None) -> str:
        return format_downtime(self.downtime_seconds(now))


@dataclass(frozen=True)
class ServiceStatus:
    host_name: str
    service_description: str
    current_state: ServiceState
    last_check: int
    last_state_change: int
    plugin_output: str


_WWAN_PROV_RE = re.compile(r"Prov:\s*(.+?)\s*IMEI:", re.IGNORECASE)

# El operador llega escrito distinto según el módem ("Claro AR", "Claro",
# "AR - Movistar", "Movistar"), así que se reconoce por palabra clave en vez de
# normalizar el string, que es frágil. COMCEL y WOM se dejan como vienen: son
# marcas distintas, no variantes de las anteriores.
_OPERADORES = ("MOVISTAR", "CLARO", "COMCEL", "WOM", "PERSONAL", "ENTEL", "TIGO")


def parse_wwan_operator(plugin_output: str) -> str | None:
    """Operador de la red según el servicio WWAN Radio. None si Nagios no lo reporta.

    Nagios expone el operador y el IMEI del módem, pero nunca el ICCID: de acá
    sale a qué red está conectado el equipo, no qué SIM tiene puesta.
    """
    match = _WWAN_PROV_RE.search(plugin_output or "")
    if not match:
        return None
    crudo = match.group(1).upper()
    for operador in _OPERADORES:
        if operador in crudo:
            return operador.capitalize()
    return None


def format_downtime(total_seconds: int) -> str:
    """Formatea segundos como `34d 6h 23m` (omite unidades en cero al frente)."""
    if total_seconds <= 0:
        return "0m"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _iter_blocks(raw_text: str, block_header: str, fields_of_interest: set[str]) -> list[dict[str, str]]:
    """Escanea status.dat de forma tolerante para un tipo de bloque dado.

    Nagios reescribe status.dat constantemente. Si lo leemos justo en ese
    momento, el archivo puede llegar truncado (por ejemplo con el último
    bloque a medio escribir). En vez de abortar todo el parseo con una
    excepción, cada bloque se valida de forma independiente: si falta un
    campo requerido o el bloque nunca cierra, se descarta con un warning
    y se sigue con el resto del archivo. Compartido entre `hoststatus` y
    `servicestatus`: son el mismo formato de bloque, solo cambian los
    campos relevantes.
    """
    blocks: list[dict[str, str]] = []
    in_block = False
    current_fields: dict[str, str] = {}
    discarded = 0

    for line in raw_text.splitlines():
        stripped = line.strip()

        if not in_block:
            if stripped == f"{block_header} {{":
                in_block = True
                current_fields = {}
            continue

        if stripped == "}":
            if current_fields.get("host_name"):
                blocks.append(current_fields)
            else:
                discarded += 1
            in_block = False
            continue

        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key in fields_of_interest:
                current_fields[key] = value.strip()

    if in_block:
        # El archivo terminó a mitad de un bloque: lectura concurrente
        # con la reescritura de Nagios. Se descarta, no es un error fatal.
        discarded += 1
        logger.warning("status.dat terminó con un bloque %s sin cerrar; se descartó.", block_header)

    if discarded:
        logger.warning("%d bloque(s) %s descartado(s) por datos incompletos.", discarded, block_header)

    return blocks


def parse_status_dat(raw_text: str) -> list[HostStatus]:
    """Extrae todos los bloques `hoststatus {...}` válidos de status.dat."""
    hosts = []
    for fields in _iter_blocks(raw_text, "hoststatus", _HOST_FIELDS):
        host = _build_host_status(fields)
        if host is not None:
            hosts.append(host)
    return hosts


def parse_service_statuses(raw_text: str) -> list[ServiceStatus]:
    """Extrae todos los bloques `servicestatus {...}` válidos de status.dat."""
    services = []
    for fields in _iter_blocks(raw_text, "servicestatus", _SERVICE_FIELDS):
        service = _build_service_status(fields)
        if service is not None:
            services.append(service)
    return services


def _build_host_status(fields: dict[str, str]) -> HostStatus | None:
    try:
        host_name = fields["host_name"]
        current_state = HostState(int(fields["current_state"]))
        last_check = int(fields.get("last_check", "0"))
        last_state_change = int(fields.get("last_state_change", "0"))
        plugin_output = fields.get("plugin_output", "")
        last_time_up = int(fields.get("last_time_up", "0"))
        last_time_down = int(fields.get("last_time_down", "0"))
    except (KeyError, ValueError) as exc:
        logger.warning("Bloque hoststatus descartado por campo inválido/faltante: %s", exc)
        return None

    if not host_name:
        return None

    return HostStatus(
        host_name=host_name,
        current_state=current_state,
        last_check=last_check,
        last_state_change=last_state_change,
        plugin_output=plugin_output,
        last_time_up=last_time_up,
        last_time_down=last_time_down,
    )


def _build_service_status(fields: dict[str, str]) -> ServiceStatus | None:
    try:
        host_name = fields["host_name"]
        service_description = fields["service_description"]
        current_state = ServiceState(int(fields["current_state"]))
        last_check = int(fields.get("last_check", "0"))
        last_state_change = int(fields.get("last_state_change", "0"))
        plugin_output = fields.get("plugin_output", "")
    except (KeyError, ValueError) as exc:
        logger.warning("Bloque servicestatus descartado por campo inválido/faltante: %s", exc)
        return None

    if not host_name or not service_description:
        return None

    return ServiceStatus(
        host_name=host_name,
        service_description=service_description,
        current_state=current_state,
        last_check=last_check,
        last_state_change=last_state_change,
        plugin_output=plugin_output,
    )
