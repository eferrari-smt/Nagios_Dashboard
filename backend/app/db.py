"""Persistencia de snapshots de estado en SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.status_parser import HostState, HostStatus, ServiceState, ServiceStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_name TEXT NOT NULL,
    current_state INTEGER NOT NULL,
    last_check INTEGER NOT NULL,
    last_state_change INTEGER NOT NULL,
    plugin_output TEXT NOT NULL,
    polled_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_host_polled ON snapshots(host_name, polled_at);

CREATE TABLE IF NOT EXISTS poll_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at INTEGER NOT NULL,
    success INTEGER NOT NULL,
    error_message TEXT,
    host_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_poll_attempts_attempted_at ON poll_attempts(attempted_at);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL,
    category TEXT
);

CREATE TABLE IF NOT EXISTS host_tags (
    host_name TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (host_name, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_host_tags_host ON host_tags(host_name);

CREATE TABLE IF NOT EXISTS service_status (
    host_name TEXT NOT NULL,
    service_description TEXT NOT NULL,
    current_state INTEGER NOT NULL,
    last_check INTEGER NOT NULL,
    last_state_change INTEGER NOT NULL,
    plugin_output TEXT NOT NULL,
    polled_at INTEGER NOT NULL,
    PRIMARY KEY (host_name, service_description)
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS host_replacements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_rak TEXT NOT NULL,
    new_rak TEXT NOT NULL,
    old_host_name TEXT,
    new_host_name TEXT NOT NULL,
    nombre_pc TEXT,
    ambiente TEXT,
    replaced_at INTEGER NOT NULL,
    motivo TEXT                  -- por qué se reemplazó; NULL en los registros previos
);
CREATE INDEX IF NOT EXISTS idx_host_replacements_new ON host_replacements(new_host_name);
CREATE INDEX IF NOT EXISTS idx_host_replacements_old ON host_replacements(old_host_name);

CREATE TABLE IF NOT EXISTS sim_consumption (
    icc TEXT PRIMARY KEY,
    consumo_mb REAL NOT NULL,
    limite_mb REAL,
    updated_at INTEGER NOT NULL
);

-- Historial de alarmas: a diferencia de `service_status` (que guarda solo el
-- estado vigente y se pisa en cada poll), acá se agrega una fila **solo cuando
-- una alarma cambia de estado**. Guardar los 7 servicios de ~228 equipos en
-- cada ciclo de 10 minutos serían ~230.000 filas por día casi todas idénticas;
-- registrando transiciones son unas decenas por día y la línea de tiempo queda
-- igual de completa.
CREATE TABLE IF NOT EXISTS service_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_name TEXT NOT NULL,
    service_description TEXT NOT NULL,
    previous_state INTEGER,          -- NULL la primera vez que se ve el servicio
    current_state INTEGER NOT NULL,
    changed_at INTEGER NOT NULL,     -- last_state_change de Nagios, no el momento del poll
    plugin_output TEXT NOT NULL,
    recorded_at INTEGER NOT NULL     -- poll en el que se detectó el cambio
);
CREATE INDEX IF NOT EXISTS idx_service_events_host ON service_events(host_name, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_service_events_servicio ON service_events(service_description, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_service_events_changed ON service_events(changed_at DESC);
"""


@dataclass(frozen=True)
class SnapshotRow:
    host_name: str
    current_state: HostState
    last_check: int
    last_state_change: int
    plugin_output: str
    polled_at: int


@dataclass(frozen=True)
class ServiceStatusRow:
    host_name: str
    service_description: str
    current_state: ServiceState
    last_check: int
    last_state_change: int
    plugin_output: str
    polled_at: int


@dataclass(frozen=True)
class ServiceEventRow:
    """Un cambio de estado de una alarma. `previous_state` es None en el alta."""
    host_name: str
    service_description: str
    previous_state: ServiceState | None
    current_state: ServiceState
    changed_at: int
    plugin_output: str
    recorded_at: int


@dataclass(frozen=True)
class ReplacementRow:
    id: int
    old_rak: str
    new_rak: str
    old_host_name: str | None
    new_host_name: str
    nombre_pc: str | None
    ambiente: str | None
    replaced_at: int
    motivo: str | None = None


@dataclass(frozen=True)
class SimConsumptionRow:
    icc: str
    consumo_mb: float
    limite_mb: float | None
    updated_at: int


@dataclass(frozen=True)
class PollAttempt:
    attempted_at: int
    success: bool
    error_message: str | None
    host_count: int | None


def get_connection(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()
    _migrate_schema(conn)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Ajustes a tablas ya existentes que `CREATE TABLE IF NOT EXISTS` no cubre.

    Una base ya creada antes de agregar una columna se queda sin ella —
    hay que agregarla a mano. Se revisa con PRAGMA table_info en vez de
    asumir la versión del esquema, para no romper una base ya viva.
    """
    tag_columns = [row[1] for row in conn.execute("PRAGMA table_info(tags)").fetchall()]
    if "category" not in tag_columns:
        conn.execute("ALTER TABLE tags ADD COLUMN category TEXT")
        conn.commit()

    repl_columns = [row[1] for row in conn.execute("PRAGMA table_info(host_replacements)").fetchall()]
    if "motivo" not in repl_columns:
        conn.execute("ALTER TABLE host_replacements ADD COLUMN motivo TEXT")
        conn.commit()


def save_snapshot(conn: sqlite3.Connection, hosts: list[HostStatus], polled_at: int) -> None:
    """Inserta una fila por host para esta corrida del poller."""
    conn.executemany(
        """
        INSERT INTO snapshots
            (host_name, current_state, last_check, last_state_change, plugin_output, polled_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (h.host_name, int(h.current_state), h.last_check, h.last_state_change, h.plugin_output, polled_at)
            for h in hosts
        ],
    )
    conn.commit()


def get_histories_since(conn: sqlite3.Connection, since: int) -> dict[str, list[SnapshotRow]]:
    """Historial reciente de **todos** los hosts, en una sola consulta.

    Antes la vista principal llamaba a `get_host_history` una vez por equipo y
    sin recorte temporal: 229 consultas que entre todas materializaban las
    ~520.000 filas de la tabla en cada carga de pantalla, unos 6 segundos. El
    clasificador de patrones nunca mira más allá de 7 días (ver
    `pattern_classifier`), así que traer todo era trabajo puro de descarte.

    Devuelve las listas ordenadas de la más vieja a la más nueva, que es lo
    que espera `classify`.
    """
    rows = conn.execute(
        "SELECT host_name, current_state, last_check, last_state_change, plugin_output, polled_at "
        "FROM snapshots WHERE polled_at >= ? ORDER BY host_name, polled_at ASC",
        (since,),
    ).fetchall()

    historias: dict[str, list[SnapshotRow]] = {}
    for r in rows:
        historias.setdefault(r[0], []).append(
            SnapshotRow(
                host_name=r[0],
                current_state=HostState(r[1]),
                last_check=r[2],
                last_state_change=r[3],
                plugin_output=r[4],
                polled_at=r[5],
            )
        )
    return historias


def get_host_history(
    conn: sqlite3.Connection, host_name: str, since: int | None = None
) -> list[SnapshotRow]:
    """Historial de un host ordenado del más viejo al más nuevo."""
    query = (
        "SELECT host_name, current_state, last_check, last_state_change, plugin_output, polled_at "
        "FROM snapshots WHERE host_name = ?"
    )
    params: list[object] = [host_name]
    if since is not None:
        query += " AND polled_at >= ?"
        params.append(since)
    query += " ORDER BY polled_at ASC"

    rows = conn.execute(query, params).fetchall()
    return [
        SnapshotRow(
            host_name=r[0],
            current_state=HostState(r[1]),
            last_check=r[2],
            last_state_change=r[3],
            plugin_output=r[4],
            polled_at=r[5],
        )
        for r in rows
    ]


def get_host_first_seen(conn: sqlite3.Connection, host_name: str) -> int | None:
    """Primer poll en el que apareció este host. None si nunca se lo vio.

    No es la fecha de alta en Nagios: es desde cuándo lo viene registrando
    el panel. Para un host anterior a la puesta en marcha del panel esta
    fecha es la del primer poll, no la del alta.
    """
    row = conn.execute("SELECT MIN(polled_at) FROM snapshots WHERE host_name = ?", (host_name,)).fetchone()
    return row[0] if row and row[0] is not None else None


def get_all_host_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT host_name FROM snapshots").fetchall()
    return [r[0] for r in rows]


STALE_HOST_SECONDS = 1800  # 3 ciclos de polling


def get_latest_snapshots(
    conn: sqlite3.Connection, stale_after_seconds: int = STALE_HOST_SECONDS
) -> dict[str, SnapshotRow]:
    """Última snapshot de cada host, para servir estado actual sin repollear en vivo.

    Deja afuera los hosts que Nagios dejó de reportar hace más de
    `stale_after_seconds`: cuando se da de baja un equipo (o se lo reemplaza),
    su .cfg desaparece del servidor y deja de aparecer en status.dat, pero su
    última snapshot queda en la base para siempre. Sin este filtro esos equipos
    seguían figurando en el panel, congelados en su último estado, y había que
    limpiarlos a mano.

    El margen es de varios ciclos a propósito: si un poll llega con el bloque
    del host truncado (status.dat se lee mientras Nagios lo reescribe), el
    equipo no debe desaparecer por una sola lectura incompleta.

    La referencia es el último poll registrado y no la hora actual, así que si
    el poller se cae no desaparece nadie de la vista.
    """
    ultimo_poll = conn.execute("SELECT MAX(polled_at) FROM snapshots").fetchone()[0]
    if ultimo_poll is None:
        return {}
    corte = ultimo_poll - stale_after_seconds

    rows = conn.execute(
        """
        SELECT s.host_name, s.current_state, s.last_check, s.last_state_change,
               s.plugin_output, s.polled_at
        FROM snapshots s
        INNER JOIN (
            SELECT host_name, MAX(polled_at) AS max_polled_at
            FROM snapshots
            GROUP BY host_name
        ) latest ON s.host_name = latest.host_name AND s.polled_at = latest.max_polled_at
        WHERE latest.max_polled_at >= ?
        """,
        (corte,),
    ).fetchall()
    return {
        r[0]: SnapshotRow(
            host_name=r[0],
            current_state=HostState(r[1]),
            last_check=r[2],
            last_state_change=r[3],
            plugin_output=r[4],
            polled_at=r[5],
        )
        for r in rows
    }


def record_poll_attempt(
    conn: sqlite3.Connection,
    attempted_at: int,
    success: bool,
    error_message: str | None = None,
    host_count: int | None = None,
) -> None:
    """Registra cada corrida del scheduler, exitosa o no.

    Sin esto, un fallo de conexión sería invisible: la última snapshot
    guardada simplemente quedaría vieja, sin forma de distinguir "Nagios
    no tiene novedades" de "el poller no pudo conectarse". Es la base del
    indicador de frescura que va a mostrar el frontend.
    """
    conn.execute(
        "INSERT INTO poll_attempts (attempted_at, success, error_message, host_count) VALUES (?, ?, ?, ?)",
        (attempted_at, int(success), error_message, host_count),
    )
    conn.commit()


def get_last_poll_attempt(conn: sqlite3.Connection) -> PollAttempt | None:
    row = conn.execute(
        "SELECT attempted_at, success, error_message, host_count "
        "FROM poll_attempts ORDER BY attempted_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return PollAttempt(attempted_at=row[0], success=bool(row[1]), error_message=row[2], host_count=row[3])


def _record_service_transitions(
    conn: sqlite3.Connection, services: list[ServiceStatus], polled_at: int
) -> int:
    """Agrega a `service_events` una fila por cada alarma que cambió de estado.

    Se llama desde `upsert_service_statuses` **antes** de pisar los datos,
    porque necesita comparar el estado entrante contra el vigente. Devuelve
    cuántas transiciones se registraron.

    Como momento del cambio se usa `last_state_change` de Nagios y no el del
    poll: si una alarma cambió a las 14:03 y el poll la ve a las 14:10, la
    línea de tiempo tiene que decir 14:03. Un servicio visto por primera vez
    se registra con `previous_state` en NULL — es alta, no transición.
    """
    previos = {
        (host_name, servicio): estado
        for host_name, servicio, estado in conn.execute(
            "SELECT host_name, service_description, current_state FROM service_status"
        )
    }

    eventos = []
    for s in services:
        clave = (s.host_name, s.service_description)
        anterior = previos.get(clave)
        if anterior is not None and anterior == int(s.current_state):
            continue  # sigue igual, no hay nada que registrar
        eventos.append((
            s.host_name, s.service_description, anterior, int(s.current_state),
            s.last_state_change, s.plugin_output, polled_at,
        ))

    if eventos:
        conn.executemany(
            "INSERT INTO service_events (host_name, service_description, previous_state, "
            "current_state, changed_at, plugin_output, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            eventos,
        )
    return len(eventos)


def upsert_service_statuses(conn: sqlite3.Connection, services: list[ServiceStatus], polled_at: int) -> int:
    """Guarda el estado vigente de cada alarma y registra las que cambiaron.

    `service_status` guarda solo el estado actual (se pisa en cada poll) para
    poder dibujar la vista de alarmas sin recorrer historial. Los cambios van
    aparte, a `service_events`, para sostener la línea de tiempo y la regla de
    "alarma sostenida más de 48 h". Devuelve la cantidad de transiciones.
    """
    transiciones = _record_service_transitions(conn, services, polled_at)
    conn.executemany(
        """
        INSERT INTO service_status
            (host_name, service_description, current_state, last_check, last_state_change, plugin_output, polled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(host_name, service_description) DO UPDATE SET
            current_state=excluded.current_state,
            last_check=excluded.last_check,
            last_state_change=excluded.last_state_change,
            plugin_output=excluded.plugin_output,
            polled_at=excluded.polled_at
        """,
        [
            (s.host_name, s.service_description, int(s.current_state), s.last_check, s.last_state_change, s.plugin_output, polled_at)
            for s in services
        ],
    )
    conn.commit()
    return transiciones


def get_service_events(
    conn: sqlite3.Connection,
    *,
    host_name: str | None = None,
    service_description: str | None = None,
    since: int | None = None,
    limit: int = 200,
) -> list[ServiceEventRow]:
    """Línea de tiempo de alarmas, de la más reciente a la más vieja.

    Sin filtros devuelve los últimos cambios de toda la red (para la vista
    general); con `host_name` arma la ficha de un equipo.
    """
    condiciones, params = [], []
    if host_name:
        condiciones.append("host_name = ?")
        params.append(host_name)
    if service_description:
        condiciones.append("service_description = ?")
        params.append(service_description)
    if since is not None:
        condiciones.append("changed_at >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    params.append(limit)

    rows = conn.execute(
        "SELECT host_name, service_description, previous_state, current_state, "
        f"changed_at, plugin_output, recorded_at FROM service_events {where} "
        "ORDER BY changed_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return [
        ServiceEventRow(
            host_name=r[0],
            service_description=r[1],
            previous_state=ServiceState(r[2]) if r[2] is not None else None,
            current_state=ServiceState(r[3]),
            changed_at=r[4],
            plugin_output=r[5],
            recorded_at=r[6],
        )
        for r in rows
    ]


def get_all_service_statuses(conn: sqlite3.Connection) -> list[ServiceStatusRow]:
    rows = conn.execute(
        "SELECT host_name, service_description, current_state, last_check, last_state_change, plugin_output, polled_at "
        "FROM service_status"
    ).fetchall()
    return [
        ServiceStatusRow(
            host_name=r[0], service_description=r[1], current_state=ServiceState(r[2]),
            last_check=r[3], last_state_change=r[4], plugin_output=r[5], polled_at=r[6],
        )
        for r in rows
    ]


def record_host_replacement(
    conn: sqlite3.Connection,
    *,
    old_rak: str,
    new_rak: str,
    old_host_name: str | None,
    new_host_name: str,
    nombre_pc: str | None,
    ambiente: str | None,
    replaced_at: int,
    motivo: str | None = None,
) -> None:
    """Deja registrado un reemplazo de equipo.

    Vive en la base y no en las Notas del inventario porque es un hecho
    histórico: las Notas se editan y se pisan, y quedan para describir
    problemas puntuales del equipo, no para llevar la trazabilidad.
    """
    conn.execute(
        """
        INSERT INTO host_replacements
            (old_rak, new_rak, old_host_name, new_host_name, nombre_pc, ambiente, replaced_at, motivo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (old_rak, new_rak, old_host_name, new_host_name, nombre_pc, ambiente, replaced_at, motivo),
    )
    conn.commit()


def get_replacement_chain(conn: sqlite3.Connection, host_name: str, limit: int = 20) -> list[ReplacementRow]:
    """Todos los reemplazos que llevaron al host actual, del más reciente al más viejo.

    Sigue la cadena hacia atrás: un equipo puede haber sido reemplazado
    varias veces, y el host anterior de un reemplazo es el actual del
    anterior. El límite corta un ciclo si los datos quedaran inconsistentes.
    """
    cadena: list[ReplacementRow] = []
    actual = host_name
    vistos = set()
    while actual and actual not in vistos and len(cadena) < limit:
        vistos.add(actual)
        row = conn.execute(
            "SELECT id, old_rak, new_rak, old_host_name, new_host_name, nombre_pc, ambiente, replaced_at, motivo "
            "FROM host_replacements WHERE new_host_name = ? ORDER BY replaced_at DESC LIMIT 1",
            (actual,),
        ).fetchone()
        if row is None:
            break
        cadena.append(_replacement_row(row))
        actual = row[3]
    return cadena


def get_all_replacements(conn: sqlite3.Connection) -> list[ReplacementRow]:
    rows = conn.execute(
        "SELECT id, old_rak, new_rak, old_host_name, new_host_name, nombre_pc, ambiente, replaced_at, motivo "
        "FROM host_replacements ORDER BY replaced_at DESC"
    ).fetchall()
    return [_replacement_row(r) for r in rows]


def _replacement_row(r) -> ReplacementRow:
    return ReplacementRow(
        id=r[0], old_rak=r[1], new_rak=r[2], old_host_name=r[3],
        new_host_name=r[4], nombre_pc=r[5], ambiente=r[6], replaced_at=r[7],
        motivo=r[8] if len(r) > 8 else None,
    )


def upsert_sim_consumption(conn: sqlite3.Connection, usages: dict, updated_at: int) -> None:
    """Guarda el consumo actual de cada SIM (no un histórico): se pisa en cada scrape diario.

    `usages` es un dict icc -> SimUsage (ver app.kite_client), no un tipo
    propio de este módulo para no acoplar db.py a Playwright/Kite.
    """
    conn.executemany(
        """
        INSERT INTO sim_consumption (icc, consumo_mb, limite_mb, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(icc) DO UPDATE SET
            consumo_mb=excluded.consumo_mb,
            limite_mb=excluded.limite_mb,
            updated_at=excluded.updated_at
        """,
        [(icc, usage.consumo_mb, usage.limite_mb, updated_at) for icc, usage in usages.items()],
    )
    conn.commit()


def get_all_sim_consumption(conn: sqlite3.Connection) -> dict[str, SimConsumptionRow]:
    rows = conn.execute("SELECT icc, consumo_mb, limite_mb, updated_at FROM sim_consumption").fetchall()
    return {
        r[0]: SimConsumptionRow(icc=r[0], consumo_mb=r[1], limite_mb=r[2], updated_at=r[3])
        for r in rows
    }
