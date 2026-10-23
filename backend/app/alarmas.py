"""Alarmas por equipo: catálogo de tipos, vista agrupada y bandeja de intervención.

Una "alarma" acá es un check de servicio de Nagios que **no** está en OK. Un
check en OK no es una alarma: es la ausencia de una, y por eso no se lista.

Los tipos y sus umbrales salen de los scripts del agente NRDP que corre en
cada CSL (`NRDP_Monitoreo/Monitor-*.ps1`), no de Nagios: Nagios solo recibe
el estado ya calculado. Si se cambia un umbral en el agente, hay que
actualizarlo acá también — es documentación, no lógica: nada de esto decide
el estado, solo lo explica en pantalla.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.dashboard_service import HostView, is_operationally_excluded
from app.status_parser import ServiceState, format_downtime

# Una alarma que lleva más de esto en CRITICAL deja de ser un evento y pasa a
# ser un problema que nadie atendió: ya no se va a resolver sola.
UMBRAL_INTERVENCION_SEGUNDOS = 48 * 3600


@dataclass(frozen=True)
class TipoAlarma:
    """Un tipo de alarma tal como lo manda el agente, con sus umbrales reales."""
    nombre: str  # exactamente como llega en service_description
    etiqueta: str
    descripcion: str
    umbral_warning: str | None
    umbral_critical: str | None


TIPOS_ALARMA: tuple[TipoAlarma, ...] = (
    TipoAlarma(
        "CPU Load", "Carga de CPU",
        "Uso promedio de todos los procesadores.",
        "más de 75%", "más de 90%",
    ),
    TipoAlarma(
        "RAM Usage", "Memoria RAM",
        "Porcentaje de memoria física en uso.",
        "más de 75%", "más de 90%",
    ),
    TipoAlarma(
        "Disk Space", "Espacio en disco",
        "Espacio libre en cada disco físico.",
        "menos de 15% libre", "menos de 5% libre",
    ),
    TipoAlarma(
        "WWAN Radio", "Radio WWAN",
        "Estado del módem y calidad de señal móvil.",
        "señal bajo -85 dBm", "desconectado o señal bajo -100 dBm",
    ),
    TipoAlarma(
        "Critical Processes", "Procesos críticos",
        "Presencia de lsass, wininit, services y explorer.",
        None, "falta alguno de los procesos",
    ),
    TipoAlarma(
        "Temperature", "Temperatura",
        "Sensores térmicos del equipo. Si el equipo no expone sensores, "
        "el agente informa OK: no distingue 'fresco' de 'no sé'.",
        "más de 70 °C", "más de 85 °C",
    ),
    TipoAlarma(
        "Security Events", "Eventos de seguridad",
        "Inicios de sesión fallidos en la última hora.",
        None, "uno o más intentos fallidos",
    ),
)

_TIPOS_POR_NOMBRE = {t.nombre: t for t in TIPOS_ALARMA}


def etiqueta_de(nombre: str) -> str:
    """Nombre legible de un tipo de alarma; si es uno nuevo, se muestra tal cual."""
    tipo = _TIPOS_POR_NOMBRE.get(nombre)
    return tipo.etiqueta if tipo else nombre


@dataclass(frozen=True)
class AlarmaView:
    host_name: str
    nombre_pc: str | None
    ambiente: str | None
    tipo: str  # service_description crudo, para filtrar
    etiqueta: str
    estado: ServiceState
    plugin_output: str
    desde: int  # last_state_change de Nagios
    duracion_segundos: int
    duracion_human: str
    requiere_intervencion: bool


def _alarma_de_servicio(host: HostView, servicio, ahora: int) -> AlarmaView:
    duracion = max(0, ahora - servicio.last_state_change)
    estado = servicio.current_state
    return AlarmaView(
        host_name=host.host_name,
        nombre_pc=host.entry.nombre_pc if host.entry else None,
        ambiente=host.entry.cliente if host.entry else None,
        tipo=servicio.service_description,
        etiqueta=etiqueta_de(servicio.service_description),
        estado=estado,
        plugin_output=servicio.plugin_output,
        desde=servicio.last_state_change,
        duracion_segundos=duracion,
        duracion_human=format_downtime(duracion),
        # Solo CRITICAL sostenida entra en la bandeja de trabajo: una lista que
        # nace con decenas de WARNING crónicos se deja de mirar a la semana.
        requiere_intervencion=(
            estado == ServiceState.CRITICAL and duracion >= UMBRAL_INTERVENCION_SEGUNDOS
        ),
    )


def get_alarmas(hosts: list[HostView], *, ahora: int | None = None) -> list[AlarmaView]:
    """Todas las alarmas activas (estado distinto de OK) de los equipos en producción.

    Se excluyen los equipos con tag de RMA/Testing/Refurbish por la misma razón
    que en el resto de la herramienta: están fuera de servicio a propósito y sus
    alarmas no son accionables.

    El orden es el de atención: primero lo que requiere intervención, después
    por gravedad y, dentro de cada gravedad, lo que lleva más tiempo roto.
    """
    ahora = ahora if ahora is not None else int(time.time())

    alarmas = [
        _alarma_de_servicio(host, servicio, ahora)
        for host in hosts
        if not is_operationally_excluded(host)
        for servicio in host.services
        if servicio.current_state != ServiceState.OK
    ]

    prioridad = {ServiceState.CRITICAL: 0, ServiceState.WARNING: 1, ServiceState.UNKNOWN: 2}
    alarmas.sort(
        key=lambda a: (
            not a.requiere_intervencion,
            prioridad.get(a.estado, 3),
            -a.duracion_segundos,
        )
    )
    return alarmas


def get_intervenciones(hosts: list[HostView], *, ahora: int | None = None) -> list[AlarmaView]:
    """Alarmas críticas sostenidas más de 48 h: la bandeja de trabajo."""
    return [a for a in get_alarmas(hosts, ahora=ahora) if a.requiere_intervencion]


@dataclass(frozen=True)
class ResumenTipo:
    """Cuántas alarmas hay de un tipo, para las tarjetas de la pestaña."""
    tipo: str
    etiqueta: str
    critical: int
    warning: int
    unknown: int
    requieren_intervencion: int

    @property
    def total(self) -> int:
        return self.critical + self.warning + self.unknown


def resumir_por_tipo(alarmas: list[AlarmaView]) -> list[ResumenTipo]:
    """Un resumen por tipo de alarma, incluyendo los tipos sin alarmas.

    Los tipos conocidos aparecen siempre y en orden fijo aunque estén en cero:
    que "Procesos críticos" desaparezca de la pantalla cuando todo está bien
    obliga a recordar qué se está vigilando. Un tipo que llegue del agente y
    no esté en el catálogo se agrega al final.
    """
    por_tipo: dict[str, list[AlarmaView]] = {t.nombre: [] for t in TIPOS_ALARMA}
    for a in alarmas:
        por_tipo.setdefault(a.tipo, []).append(a)

    return [
        ResumenTipo(
            tipo=tipo,
            etiqueta=etiqueta_de(tipo),
            critical=sum(1 for a in lista if a.estado == ServiceState.CRITICAL),
            warning=sum(1 for a in lista if a.estado == ServiceState.WARNING),
            unknown=sum(1 for a in lista if a.estado == ServiceState.UNKNOWN),
            requieren_intervencion=sum(1 for a in lista if a.requiere_intervencion),
        )
        for tipo, lista in por_tipo.items()
    ]


def detectar_agentes_mudos(hosts: list[HostView]) -> list[HostView]:
    """Equipos que figuran arriba pero cuyas alarmas están todas en UNKNOWN.

    Un equipo así no está sano: su agente NRDP dejó de reportar y Nagios sigue
    mostrando el último estado conocido del host. Sin esto quedan invisibles,
    porque no aparecen como caídos ni como alarma concreta.
    """
    mudos = []
    for host in hosts:
        if is_operationally_excluded(host) or not host.services:
            continue
        if all(s.current_state == ServiceState.UNKNOWN for s in host.services):
            mudos.append(host)
    return mudos
