"""Modelos de respuesta de la API (Pydantic)."""

from __future__ import annotations

from pydantic import BaseModel


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class TagOut(BaseModel):
    id: int
    name: str
    color: str
    category: str | None = None


class TagCreateIn(BaseModel):
    name: str
    color: str
    category: str | None = None


class TagCategoryIn(BaseModel):
    category: str | None = None


class TagHostsIn(BaseModel):
    host_names: list[str]


class HostEditIn(BaseModel):
    nombre_pc: str | None = None
    notas: str | None = None
    sim: str | None = None
    empresa: str | None = None
    ambiente: str | None = None
    estado: str | None = None


class HostReplaceIn(BaseModel):
    nombre_pc: str
    rak: str
    anydesk_id: str
    crd_host_id: str | None = None
    cliente: str | None = None


class HostReplaceOut(BaseModel):
    old_rak: str
    old_host_name: str | None = None
    new_host_name: str | None = None
    nagios_applied: bool
    old_removed: bool


class HostRegisterIn(BaseModel):
    rak: str
    nombre_pc: str = ""
    cliente: str = ""
    anydesk_id: str = ""
    crd_host_id: str = ""
    notas: str = ""


class HostRegisterOut(BaseModel):
    host_name: str
    remote_path: str
    inventory_row_created: bool
    validation_output: str


class ServiceStatusOut(BaseModel):
    service_description: str
    current_state: str
    last_check: int
    last_state_change: int
    plugin_output: str


class HostStatusOut(BaseModel):
    host_name: str
    current_state: str
    downtime_seconds: int
    downtime_human: str
    plugin_output: str
    last_check: int
    polled_at: int
    pattern: str
    match_type: str
    nombre_pc: str | None = None
    ambiente: str | None = None
    anydesk_id: str | None = None
    rak: str | None = None
    crd_host_id: str | None = None
    notas: str | None = None
    sim: str | None = None
    empresa: str | None = None
    estado: str | None = None
    consumo_mb: float | None = None
    limite_mb: float | None = None
    consumo_pct: float | None = None
    consumo_updated_at: int | None = None
    tags: list[TagOut] = []
    services: list[ServiceStatusOut] = []


class ReplacementOut(BaseModel):
    id: int
    old_rak: str
    new_rak: str
    old_host_name: str | None = None
    new_host_name: str
    nombre_pc: str | None = None
    ambiente: str | None = None
    replaced_at: int


class HostNagiosInfoOut(BaseModel):
    host_name: str
    cfg_exists: bool
    cfg_modified_at: int | None = None
    first_seen_at: int | None = None
    ssh_error: str | None = None


class SnapshotOut(BaseModel):
    current_state: str
    last_check: int
    last_state_change: int
    plugin_output: str
    polled_at: int


class DuplicateEntryOut(BaseModel):
    row_number: int
    cliente: str
    nombre_pc: str


class DuplicateGroupOut(BaseModel):
    anydesk_id: str
    entries: list[DuplicateEntryOut]


class MetaOut(BaseModel):
    last_polled_at: int | None
    host_count: int
    down_count: int
    unmatched_count: int
    last_poll_success: bool | None
    last_poll_error: str | None


class InventoryEntryCreateIn(BaseModel):
    nombre_pc: str
    rak: str
    cliente: str
    anydesk_id: str = ""
    crd_host_id: str = ""
    notas: str = ""


class NagiosAddPlanIn(BaseModel):
    rak: str


class NagiosAddPlanOut(BaseModel):
    host_name: str
    filename: str
    remote_path: str
    content: str


class NagiosRemovePlanIn(BaseModel):
    host_name: str


class NagiosRemovePlanOut(BaseModel):
    host_name: str
    filename: str
    remote_path: str
    current_content: str | None
    existed: bool


class NagiosExecuteResultOut(BaseModel):
    host_name: str
    filename: str
    remote_path: str
    validation_output: str


class PendingInventoryHostOut(BaseModel):
    host_name: str
    nombre_pc: str
    ambiente: str
    rak: str
    estado: str = "Produccion"
    tags: list[TagOut] = []


class InsightsOut(BaseModel):
    host_up: int
    host_down: int
    host_unreachable: int
    service_ok: int
    service_warning: int
    service_critical: int
    service_unknown: int


# --- Alarmas por equipo ---

class AlarmaOut(BaseModel):
    host_name: str
    nombre_pc: str | None = None
    ambiente: str | None = None
    tipo: str
    etiqueta: str
    estado: int  # 1=WARNING, 2=CRITICAL, 3=UNKNOWN (0=OK nunca se devuelve)
    plugin_output: str
    desde: int
    duracion_segundos: int
    duracion_human: str
    requiere_intervencion: bool


class ResumenTipoOut(BaseModel):
    tipo: str
    etiqueta: str
    critical: int
    warning: int
    unknown: int
    requieren_intervencion: int
    total: int


class TipoAlarmaOut(BaseModel):
    """Catálogo: qué mide cada alarma y con qué umbrales la dispara el agente."""
    nombre: str
    etiqueta: str
    descripcion: str
    umbral_warning: str | None = None
    umbral_critical: str | None = None


class AgenteMudoOut(BaseModel):
    host_name: str
    nombre_pc: str | None = None
    ambiente: str | None = None
    estado_host: str


class AlarmasOut(BaseModel):
    alarmas: list[AlarmaOut]
    resumen: list[ResumenTipoOut]
    intervenciones: list[AlarmaOut]
    agentes_mudos: list[AgenteMudoOut]
    tipos: list[TipoAlarmaOut]


class ServiceEventOut(BaseModel):
    host_name: str
    service_description: str
    etiqueta: str
    previous_state: int | None = None
    current_state: int
    changed_at: int
    plugin_output: str


# --- Estado operativo e inventario ---

class EstadoIn(BaseModel):
    estado: str


class EstadoOpcionOut(BaseModel):
    valor: str
    etiqueta: str
