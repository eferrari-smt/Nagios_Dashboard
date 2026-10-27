"""Combina snapshots (SQLite) + inventario (Excel) + clasificación de patrón.

Lee siempre del último snapshot guardado, nunca conecta a la EC2 en vivo:
la lectura por SSH tarda decenas de segundos (~2 MB vía SFTP), así que
hacerla en cada request de la API dejaría el dashboard inutilizable. El
polling en vivo es responsabilidad del scheduler (Fase 5); esta capa solo
sirve lo último que ese poller haya guardado.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from app.config import load_database_path, load_inventory_csv_path
from app.db import (
    ServiceStatusRow,
    SnapshotRow,
    get_all_host_names,
    get_all_service_statuses,
    get_all_sim_consumption,
    get_connection,
    ReplacementRow,
    get_all_replacements,
    get_host_first_seen,
    get_histories_since,
    get_host_history,
    get_replacement_chain,
    record_host_replacement,
    get_last_poll_attempt,
    get_latest_snapshots,
)
from app.inventory_loader import InventoryEntry, load_inventory
from app.inventory_writer import (
    append_inventory_row,
    delete_inventory_row,
    replace_inventory_row,
    update_inventory_row,
)
from app.matching import DuplicateGroup, MatchType, find_inventory_duplicates, match_host, normalize
from app.nagios_uploader import (
    InvalidRakError,
    derive_host_name,
    execute_add_host,
    execute_replace_host,
    get_host_cfg_mtime,
)
from app.ssh_client import NagiosSSHError
from app.pattern_classifier import Pattern, classify

# Ventana de historial que se trae para clasificar patrones. El clasificador
# usa 7 días como máximo (INTERMITTENT_WINDOW_SECONDS); se pide uno más de
# margen para no quedar justo en el borde entre polls.
VENTANA_PATRON_SEGUNDOS = 8 * 86400
from app.status_parser import HostState, ServiceState, format_downtime, parse_wwan_operator
from app.tags_store import (
    EXCLUDED_TAG_CATEGORIES,
    Tag,
    assign_tag,
    create_tag,
    delete_tag,
    get_host_tag_ids,
    list_tags,
    unassign_tag,
    update_tag_category,
)


@dataclass(frozen=True)
class HostView:
    host_name: str
    current_state: HostState
    downtime_seconds: int
    downtime_human: str
    plugin_output: str
    last_check: int
    polled_at: int
    pattern: Pattern
    match_type: MatchType
    entry: InventoryEntry | None
    tags: list[Tag]
    services: list[ServiceStatusRow]
    consumo_mb: float | None = None
    limite_mb: float | None = None
    consumo_updated_at: int | None = None
    operador_nagios: str | None = None  # operador que reporta el servicio WWAN Radio


class InventoryUnavailableError(RuntimeError):
    """El Excel de inventario no se pudo cargar (ruta inválida o archivo corrupto)."""


class HostNotEditableError(RuntimeError):
    """El host no tiene una fila propia en el inventario (sin match) — no hay nada que editar."""


def is_operationally_excluded(host: HostView) -> bool:
    """True si el equipo no está en servicio y por lo tanto no es una alarma real.

    Manda la columna `Estado` del inventario: un equipo en Testing, RMA,
    Refurbish o Baja está inactivo a propósito y se muestra solo en STOCK.

    Antes esto lo decidía un tag de categoría especial, pero ese modelo no
    tenía paso de graduación y los equipos quedaban encerrados en STOCK sin
    forma de sacarlos (ver `EstadoOperativo`). El tag sigue valiendo **solo**
    para hosts que no tienen fila en el inventario: ahí no hay columna Estado
    donde mirar, y sacarles la exclusión los haría aparecer de golpe como
    alarmas nuevas. En cuanto se les carga la ficha, manda el Estado.
    """
    if host.entry is not None:
        return not host.entry.estado.es_productivo
    return any(t.category in EXCLUDED_TAG_CATEGORIES for t in host.tags)


def _downtime(snap: SnapshotRow) -> tuple[int, str]:
    if snap.current_state == HostState.UP:
        return 0, "-"
    seconds = max(0, int(time.time()) - snap.last_state_change)
    return seconds, format_downtime(seconds)


def _load_inventory_or_raise() -> list[InventoryEntry]:
    try:
        return load_inventory(load_inventory_csv_path())
    except (FileNotFoundError, RuntimeError) as exc:
        raise InventoryUnavailableError(str(exc)) from exc


def get_current_hosts() -> list[HostView]:
    conn = get_connection(load_database_path())
    try:
        latest = get_latest_snapshots(conn)
        # El clasificador no mira más de 7 días; se pide una ventana de 8 por
        # margen y se trae todo de una, en vez de una consulta por equipo.
        historias = get_histories_since(conn, int(time.time()) - VENTANA_PATRON_SEGUNDOS)
        inventory = _load_inventory_or_raise()
        all_tags = {tag.id: tag for tag in list_tags(conn)}
        host_tag_ids = get_host_tag_ids(conn)
        sim_consumption = get_all_sim_consumption(conn)
        services_by_host: dict[str, list[ServiceStatusRow]] = {}
        for s in get_all_service_statuses(conn):
            services_by_host.setdefault(s.host_name, []).append(s)

        views = []
        for host_name, snap in latest.items():
            match = match_host(host_name, inventory)
            pattern = classify(historias.get(host_name, []))
            seconds, human = _downtime(snap)
            host_tags = [all_tags[tid] for tid in host_tag_ids.get(host_name, []) if tid in all_tags]
            consumption = sim_consumption.get(match.entry.sim) if match.entry and match.entry.sim else None
            host_services = services_by_host.get(host_name, [])
            operador = next(
                (parse_wwan_operator(s.plugin_output) for s in host_services if "WWAN" in s.service_description.upper()),
                None,
            )
            views.append(
                HostView(
                    host_name=host_name,
                    current_state=snap.current_state,
                    downtime_seconds=seconds,
                    downtime_human=human,
                    plugin_output=snap.plugin_output,
                    last_check=snap.last_check,
                    polled_at=snap.polled_at,
                    pattern=pattern,
                    match_type=match.match_type,
                    entry=match.entry,
                    tags=host_tags,
                    services=host_services,
                    operador_nagios=operador,
                    consumo_mb=consumption.consumo_mb if consumption else None,
                    limite_mb=consumption.limite_mb if consumption else None,
                    consumo_updated_at=consumption.updated_at if consumption else None,
                )
            )
    finally:
        conn.close()

    views.sort(key=lambda v: (v.current_state == HostState.UP, v.host_name))
    return views


@dataclass(frozen=True)
class PendingInventoryHost:
    host_name: str
    nombre_pc: str
    ambiente: str
    rak: str
    tags: list[Tag]
    estado: str


def get_pending_inventory_hosts() -> list[PendingInventoryHost]:
    """Filas del inventario que nunca reportaron a Nagios (ej. equipos en stock,
    todavía no dados de alta ni conectados) — hoy son 100% invisibles en el
    resto de la herramienta, porque todo se arma a partir de los snapshots de
    Nagios.

    Para decidir si una fila "ya reporta", se reusa `match_host` (el mismo
    algoritmo de truncado/substring que cruza a Nagios contra el inventario
    en todos lados) probando cada host_name conocido contra esa fila sola —
    no alcanza con comparar por igualdad, porque no todos los hosts de Nagios
    siguen la convención de RAK truncado a 15 (ej. una VM de test dada de
    alta con un nombre a mano).

    El host_name que se expone para poder taggear se deriva igual que en
    Nagios Uploader, para que si el equipo arranca a reportar más adelante,
    el host_name coincida solo y los tags ya asignados sigan aplicando.
    """
    conn = get_connection(load_database_path())
    try:
        known_host_names = get_all_host_names(conn)
        all_tags = {tag.id: tag for tag in list_tags(conn)}
        host_tag_ids = get_host_tag_ids(conn)
    finally:
        conn.close()

    inventory = _load_inventory_or_raise()
    pending = []
    seen_host_names = set()
    for entry in inventory:
        already_known = any(
            match_host(known, [entry]).match_type != MatchType.NO_MATCH for known in known_host_names
        )
        if already_known:
            continue

        identifier = entry.rak or entry.nombre_pc
        try:
            host_name = derive_host_name(identifier)
        except InvalidRakError:
            continue
        if host_name in seen_host_names:
            continue
        seen_host_names.add(host_name)
        tags = [all_tags[tid] for tid in host_tag_ids.get(host_name, []) if tid in all_tags]
        pending.append(PendingInventoryHost(
            host_name=host_name, nombre_pc=entry.nombre_pc, ambiente=entry.cliente,
            rak=entry.rak, tags=tags, estado=entry.estado.value,
        ))
    return pending


@dataclass(frozen=True)
class HostNagiosInfo:
    host_name: str
    cfg_exists: bool
    cfg_modified_at: int | None  # fecha real del alta en Nagios (mtime del .cfg)
    first_seen_at: int | None  # desde cuándo lo registra el panel
    ssh_error: str | None  # si no se pudo consultar el servidor, se informa sin fallar


def get_host_nagios_info(host_name: str) -> HostNagiosInfo:
    """Datos de alta del host: fecha del .cfg en el servidor + primer poll registrado.

    La consulta SSH puede fallar (servidor caído, red) sin que eso deba
    romper la vista de detalle: en ese caso se devuelve el error como texto
    y el resto de la información igual.
    """
    conn = get_connection(load_database_path())
    try:
        first_seen = get_host_first_seen(conn, host_name)
    finally:
        conn.close()

    cfg_mtime, ssh_error = None, None
    try:
        cfg_mtime = get_host_cfg_mtime(host_name)
    except NagiosSSHError as exc:
        ssh_error = str(exc)

    return HostNagiosInfo(
        host_name=host_name,
        cfg_exists=cfg_mtime is not None,
        cfg_modified_at=cfg_mtime,
        first_seen_at=first_seen,
        ssh_error=ssh_error,
    )


def get_host_replacements(host_name: str) -> list[ReplacementRow]:
    """Cadena de reemplazos que llevaron al equipo actual, del más reciente al más viejo."""
    conn = get_connection(load_database_path())
    try:
        return get_replacement_chain(conn, host_name)
    finally:
        conn.close()


def get_replacements() -> list[ReplacementRow]:
    conn = get_connection(load_database_path())
    try:
        return get_all_replacements(conn)
    finally:
        conn.close()


def get_host_history_view(host_name: str, since: int | None = None) -> list[SnapshotRow]:
    conn = get_connection(load_database_path())
    try:
        return get_host_history(conn, host_name, since=since)
    finally:
        conn.close()


def get_inventory_duplicate_groups() -> list[DuplicateGroup]:
    inventory = _load_inventory_or_raise()
    return find_inventory_duplicates(inventory)


@dataclass(frozen=True)
class DashboardMeta:
    last_polled_at: int | None
    host_count: int
    down_count: int
    unmatched_count: int
    last_poll_success: bool | None
    last_poll_error: str | None


def get_meta() -> DashboardMeta:
    conn = get_connection(load_database_path())
    try:
        latest = get_latest_snapshots(conn)
        last_attempt = get_last_poll_attempt(conn)
    finally:
        conn.close()

    last_poll_success = last_attempt.success if last_attempt else None
    last_poll_error = last_attempt.error_message if last_attempt else None

    if not latest:
        return DashboardMeta(
            last_polled_at=None, host_count=0, down_count=0, unmatched_count=0,
            last_poll_success=last_poll_success, last_poll_error=last_poll_error,
        )

    try:
        inventory = _load_inventory_or_raise()
        unmatched_count = sum(
            1 for host_name in latest if match_host(host_name, inventory).match_type == MatchType.NO_MATCH
        )
    except InventoryUnavailableError:
        unmatched_count = 0

    return DashboardMeta(
        last_polled_at=max(s.polled_at for s in latest.values()),
        host_count=len(latest),
        down_count=sum(1 for s in latest.values() if s.current_state != HostState.UP),
        unmatched_count=unmatched_count,
        last_poll_success=last_poll_success,
        last_poll_error=last_poll_error,
    )


@dataclass(frozen=True)
class DashboardInsights:
    host_up: int
    host_down: int
    host_unreachable: int
    service_ok: int
    service_warning: int
    service_critical: int
    service_unknown: int


def get_insights() -> DashboardInsights:
    """Totales estilo Nagios (Host/Service Status Totals) para el Dashboard.

    Usa exactamente el mismo criterio de exclusión que el resto de la
    herramienta (`is_operationally_excluded`): manda el estado operativo del
    inventario y el tag solo vale para hosts sin ficha.

    Antes esto excluía por tag para no depender del inventario y poder seguir
    mostrando totales aunque el CSV estuviera inaccesible. Esa independencia
    salía cara: cuando el modelo pasó a usar el estado, el número grande y las
    tarjetas por ambiente empezaron a contar distinto (222 contra 225) y un
    panel cuyos números no cierran no sirve para decidir nada. Si el
    inventario no está disponible, ahora el Dashboard avisa del problema en
    vez de mostrar un total que no coincide con el resto de la pantalla.
    """
    hosts = get_current_hosts()
    vigentes = [h for h in hosts if not is_operationally_excluded(h)]
    nombres = {h.host_name for h in vigentes}

    latest = {h.host_name: h for h in vigentes}
    conn = get_connection(load_database_path())
    try:
        services = [s for s in get_all_service_statuses(conn) if s.host_name in nombres]
    finally:
        conn.close()

    return DashboardInsights(
        host_up=sum(1 for s in latest.values() if s.current_state == HostState.UP),
        host_down=sum(1 for s in latest.values() if s.current_state == HostState.DOWN),
        host_unreachable=sum(1 for s in latest.values() if s.current_state == HostState.UNREACHABLE),
        service_ok=sum(1 for s in services if s.current_state == ServiceState.OK),
        service_warning=sum(1 for s in services if s.current_state == ServiceState.WARNING),
        service_critical=sum(1 for s in services if s.current_state == ServiceState.CRITICAL),
        service_unknown=sum(1 for s in services if s.current_state == ServiceState.UNKNOWN),
    )


def get_tags() -> list[Tag]:
    conn = get_connection(load_database_path())
    try:
        return list_tags(conn)
    finally:
        conn.close()


def create_new_tag(name: str, color: str, category: str | None = None) -> Tag:
    conn = get_connection(load_database_path())
    try:
        return create_tag(conn, name, color, category)
    finally:
        conn.close()


def delete_existing_tag(tag_id: int) -> None:
    conn = get_connection(load_database_path())
    try:
        delete_tag(conn, tag_id)
    finally:
        conn.close()


def update_existing_tag_category(tag_id: int, category: str | None) -> None:
    conn = get_connection(load_database_path())
    try:
        update_tag_category(conn, tag_id, category)
    finally:
        conn.close()


def assign_tag_to_hosts(tag_id: int, host_names: list[str]) -> None:
    conn = get_connection(load_database_path())
    try:
        assign_tag(conn, tag_id, host_names)
    finally:
        conn.close()


def unassign_tag_from_hosts(tag_id: int, host_names: list[str]) -> None:
    conn = get_connection(load_database_path())
    try:
        unassign_tag(conn, tag_id, host_names)
    finally:
        conn.close()


def edit_host_inventory(
    host_name: str,
    nombre_pc: str | None = None,
    notas: str | None = None,
    sim: str | None = None,
    empresa: str | None = None,
    ambiente: str | None = None,
    estado: str | None = None,
) -> None:
    """Edita Nombre PC/Notas/SIM/Empresa/Ambiente/Estado de un host directo en el inventario (nunca el RAK).

    El inventario es la fuente de verdad para estos campos a pedido del
    usuario: en vez de editarlo a mano, lo hace desde el dashboard y
    queda reflejado ahí mismo. Requiere que el host tenga match en el
    inventario — si no tiene fila propia, no hay nada que editar. El RAK
    queda afuera a propósito: para eso está "Reemplazar" (ver
    `replace_host_inventory`).
    """
    inventory = _load_inventory_or_raise()
    match = match_host(host_name, inventory)
    if match.entry is None:
        raise HostNotEditableError(
            f"'{host_name}' no tiene una fila propia en el inventario (sin match) — no se puede editar."
        )

    update_inventory_row(
        load_inventory_csv_path(),
        match.entry.row_number,
        nombre_pc=nombre_pc,
        notas=notas,
        sim=sim,
        empresa=empresa,
        cliente=ambiente,
        estado=estado,
    )


class InventoryRowNotFoundError(RuntimeError):
    """No hay ninguna fila de inventario con ese RAK."""


class HostActivoError(RuntimeError):
    """El equipo sigue reportando a Nagios: borrarlo del inventario lo dejaría huérfano."""


def _buscar_por_rak(rak: str) -> InventoryEntry:
    """Ubica la fila de inventario de un equipo por su RAK.

    Cae a "Nombre PC" si no hay match por RAK: varias filas de STOCK tienen el
    RAK cargado como nombre y la columna RAK vacía (ver `match_host`, que usa
    el mismo respaldo). Sin esto esos equipos quedan sin forma de cambiarles la
    situación ni de borrarlos, que es justo lo que hay que poder hacer con un
    equipo de depósito.
    """
    inventory = _load_inventory_or_raise()
    objetivo = normalize(rak)
    if not objetivo:
        raise InventoryRowNotFoundError("Hace falta un RAK o nombre para identificar la fila.")

    for entry in inventory:
        if entry.rak and normalize(entry.rak) == objetivo:
            return entry
    for entry in inventory:
        if not entry.rak and normalize(entry.nombre_pc) == objetivo:
            return entry
    raise InventoryRowNotFoundError(f"No hay ninguna fila de inventario con el RAK '{rak}'.")


def set_inventory_estado(rak: str, estado: str) -> None:
    """Cambia el estado operativo de un equipo, esté o no dado de alta en Nagios.

    Se busca por RAK y no por host_name porque los equipos de STOCK todavía no
    tienen host en Nagios: es el único identificador que siempre existe.
    """
    entry = _buscar_por_rak(rak)
    update_inventory_row(load_inventory_csv_path(), entry.row_number, estado=estado)


def delete_inventory_entry(rak: str) -> str:
    """Borra del inventario un equipo que ya no existe. Devuelve su Nombre PC.

    Se niega a borrar algo que siga reportando a Nagios: quedaría un host sin
    ficha, que es justamente el tipo de inconsistencia que esta herramienta
    existe para evitar. Para esos casos primero hay que darlo de baja en el
    Uploader.
    """
    entry = _buscar_por_rak(rak)

    conn = get_connection(load_database_path())
    try:
        known_host_names = get_all_host_names(conn)
    finally:
        conn.close()

    reportando = next(
        (h for h in known_host_names if match_host(h, [entry]).match_type != MatchType.NO_MATCH),
        None,
    )
    if reportando is not None:
        raise HostActivoError(
            f"'{entry.nombre_pc or rak}' sigue dado de alta en Nagios como '{reportando}'. "
            "Primero dalo de baja desde el Uploader y después borralo del inventario."
        )

    return delete_inventory_row(load_inventory_csv_path(), entry.row_number, expected_rak=entry.rak)


@dataclass(frozen=True)
class ReplaceResult:
    old_rak: str
    old_host_name: str | None
    new_host_name: str | None
    nagios_applied: bool  # False si el RAK no cambió: no hubo nada que hacer en Nagios
    old_removed: bool
    validation_output: str


# Por qué se reemplazó un equipo. El valor se guarda como texto libre en la
# base a propósito: la lista de acá puede cambiar sin migrar nada ni invalidar
# los registros viejos, y sirve para contar reemplazos por causa.
MOTIVOS_REEMPLAZO: tuple[tuple[str, str], ...] = (
    ("refurbish", "Refurbish programado"),
    ("falla_pc", "Mal funcionamiento de la PC"),
    ("cambio_nuc", "Reemplazo de NUC"),
    ("falla_disco", "Falla de disco"),
    ("falla_modem", "Falla de módem o SIM"),
    ("vandalismo", "Vandalismo o robo"),
    ("obra", "Obra o reubicación del gabinete"),
    ("otro", "Otro"),
)

MOTIVOS_VALIDOS = {clave for clave, _ in MOTIVOS_REEMPLAZO}


def etiqueta_motivo(clave: str | None) -> str | None:
    """Nombre legible de un motivo. Un valor viejo o desconocido se muestra tal cual."""
    if not clave:
        return None
    return dict(MOTIVOS_REEMPLAZO).get(clave, clave)


def replace_host_inventory(
    host_name: str,
    *,
    nombre_pc: str,
    rak: str,
    anydesk_id: str,
    crd_host_id: str | None = None,
    cliente: str | None = None,
    motivo: str | None = None,
) -> ReplaceResult:
    """Reemplazo completo de un host — única vía que toca el RAK, y ahora también Nagios.

    A diferencia de `edit_host_inventory`, esto sí puede cambiar el RAK:
    está pensado para cuando se reemplaza físicamente el CSL de un sitio.

    Cuando el RAK cambia, el cambio se aplica en Nagios también (baja del
    .cfg viejo + alta del nuevo, en un solo ciclo con validación y reload).
    Antes esto solo tocaba el CSV, lo que dejaba el host viejo reportando a
    Nagios sin fila en el inventario (aparecía como "sin identificar") y el
    equipo nuevo invisible en toda la herramienta hasta darlo de alta a mano.

    El RAK anterior queda anotado en las Notas del equipo: al sobreescribir
    la fila se perdía el único registro que existía de él.

    Si Nagios falla, el CSV se revierte a sus valores originales para que no
    queden inventario y servidor desincronizados.
    """
    inventory = _load_inventory_or_raise()
    match = match_host(host_name, inventory)
    if match.entry is None:
        raise HostNotEditableError(
            f"'{host_name}' no tiene una fila propia en el inventario (sin match) — no se puede reemplazar."
        )

    entry = match.entry
    csv_path = load_inventory_csv_path()
    rak_changed = normalize(rak) != normalize(entry.rak)

    # Las Notas quedan intactas: el reemplazo se registra en la base, para que
    # el campo libre siga sirviendo para describir problemas del equipo.
    replace_inventory_row(
        csv_path, entry.row_number,
        nombre_pc=nombre_pc, rak=rak, anydesk_id=anydesk_id,
        crd_host_id=crd_host_id, cliente=cliente,
    )

    if not rak_changed:
        return ReplaceResult(
            old_rak=entry.rak, old_host_name=None, new_host_name=None,
            nagios_applied=False, old_removed=False, validation_output="",
        )

    try:
        result = execute_replace_host(entry.rak, rak)
    except Exception:
        replace_inventory_row(
            csv_path, entry.row_number,
            nombre_pc=entry.nombre_pc, rak=entry.rak, anydesk_id=entry.anydesk_id,
            crd_host_id=entry.crd_host_id, cliente=entry.cliente, notas=entry.notas,
        )
        raise

    conn = get_connection(load_database_path())
    try:
        record_host_replacement(
            conn,
            old_rak=entry.rak, new_rak=rak,
            old_host_name=result.old_host_name, new_host_name=result.new_host_name,
            nombre_pc=nombre_pc, ambiente=cliente or entry.cliente,
            replaced_at=int(time.time()), motivo=motivo,
        )
    finally:
        conn.close()

    return ReplaceResult(
        old_rak=entry.rak, old_host_name=result.old_host_name, new_host_name=result.new_host_name,
        nagios_applied=True, old_removed=result.old_removed, validation_output=result.validation_output,
    )


def create_inventory_entry(
    *, nombre_pc: str, rak: str, cliente: str, anydesk_id: str = "", crd_host_id: str = "", notas: str = "",
) -> None:
    """Alta de un equipo nuevo en el inventario — ej. resolver un host de Inconsistencias sin fila propia."""
    append_inventory_row(
        load_inventory_csv_path(),
        nombre_pc=nombre_pc,
        rak=rak,
        cliente=cliente,
        anydesk_id=anydesk_id,
        crd_host_id=crd_host_id,
        notas=notas,
    )


class MissingInventoryDataError(RuntimeError):
    """Falta Nombre PC o Ambiente para poder crear la fila de inventario de un RAK nuevo."""


@dataclass(frozen=True)
class HostRegistrationResult:
    host_name: str
    remote_path: str
    inventory_row_created: bool
    validation_output: str


def register_host(
    *, rak: str, nombre_pc: str = "", cliente: str = "",
    anydesk_id: str = "", crd_host_id: str = "", notas: str = "",
) -> HostRegistrationResult:
    """Alta completa de un equipo: fila en el inventario (si hace falta) + .cfg en Nagios.

    Si el RAK ya está en el inventario, no se duplica la fila: solo se da de
    alta en Nagios. Si no está, se crea la fila y para eso hacen falta al
    menos Nombre PC y Ambiente.

    El inventario se escribe primero a propósito: si después falla Nagios, la
    fila queda cargada sin .cfg, que es exactamente el estado de cualquier
    equipo todavía no dado de alta (visible en la pestaña STOCK) — benigno y
    reintentable. Al revés dejaría a Nagios monitoreando un equipo que no
    existe en el inventario.
    """
    inventory = _load_inventory_or_raise()
    normalized_rak = normalize(rak)
    already_in_inventory = any(normalize(e.rak) == normalized_rak for e in inventory)

    row_created = False
    if not already_in_inventory:
        if not (nombre_pc.strip() and cliente.strip()):
            raise MissingInventoryDataError(
                f"El RAK '{rak}' no está en el inventario todavía — hacen falta Nombre PC y Ambiente "
                f"para poder cargarlo."
            )
        append_inventory_row(
            load_inventory_csv_path(),
            nombre_pc=nombre_pc, rak=rak, cliente=cliente,
            anydesk_id=anydesk_id, crd_host_id=crd_host_id, notas=notas,
        )
        row_created = True

    result = execute_add_host(rak)
    return HostRegistrationResult(
        host_name=result.host_name, remote_path=result.remote_path,
        inventory_row_created=row_created, validation_output=result.validation_output,
    )
