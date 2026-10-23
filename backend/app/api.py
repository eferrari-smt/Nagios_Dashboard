"""API FastAPI: expone el estado cruzado (Nagios + inventario + patrón).

Correr con:
    uvicorn app.api:app --reload --port 8000

Documentación automática en /docs (Swagger UI) y /redoc.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    authenticate,
    create_session,
    delete_session,
    get_session_user,
    set_password,
)
from app.config import load_cookie_secure, load_database_path
from app.db import get_connection, get_service_events
from app.alarmas import (
    TIPOS_ALARMA,
    detectar_agentes_mudos,
    etiqueta_de,
    get_alarmas,
    resumir_por_tipo,
)
from app.inventory_loader import EstadoOperativo
from app.dashboard_service import (
    HostActivoError,
    InventoryRowNotFoundError,
    delete_inventory_entry,
    set_inventory_estado,
    HostNotEditableError,
    InventoryUnavailableError,
    MissingInventoryDataError,
    register_host,
    assign_tag_to_hosts,
    create_inventory_entry,
    create_new_tag,
    delete_existing_tag,
    edit_host_inventory,
    get_current_hosts,
    get_host_history_view,
    get_host_nagios_info,
    get_host_replacements,
    get_replacements,
    get_insights,
    get_inventory_duplicate_groups,
    get_meta,
    get_pending_inventory_hosts,
    get_tags,
    replace_host_inventory,
    unassign_tag_from_hosts,
    update_existing_tag_category,
)
from app.html_report import build_html_report
from app.inventory_writer import InventoryWriteError
from app.nagios_uploader import (
    InvalidRakError,
    NagiosHostNotFoundError,
    NagiosReloadError,
    NagiosValidationError,
    execute_add_host,
    execute_remove_host,
    prepare_add_host,
    prepare_remove_host,
)
from app.scheduler import run_poll_job
from app.schemas import (
    AgenteMudoOut,
    AlarmaOut,
    AlarmasOut,
    EstadoIn,
    EstadoOpcionOut,
    ResumenTipoOut,
    ServiceEventOut,
    TipoAlarmaOut,
    DuplicateEntryOut,
    DuplicateGroupOut,
    HostEditIn,
    HostNagiosInfoOut,
    HostRegisterIn,
    HostRegisterOut,
    HostReplaceIn,
    HostReplaceOut,
    HostStatusOut,
    InsightsOut,
    InventoryEntryCreateIn,
    MetaOut,
    NagiosAddPlanIn,
    NagiosAddPlanOut,
    NagiosExecuteResultOut,
    NagiosRemovePlanIn,
    NagiosRemovePlanOut,
    PendingInventoryHostOut,
    ReplacementOut,
    ServiceStatusOut,
    SnapshotOut,
    TagCategoryIn,
    TagCreateIn,
    TagHostsIn,
    ChangePasswordIn,
    LoginIn,
    TagOut,
)
from app.ssh_client import NagiosSSHError
from app.tags_store import DuplicateTagNameError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("api")

# Límite estándar de datos por SIM: 5GB para todas, sin importar lo que Kite
# reporte como "Límite (con corte)" — ese valor se amplía a mano por SIM
# cuando un equipo tiene problemas propios que le hacen consumir de más, así
# que no sirve como referencia de "consumo normal". El % y el rojo de la
# columna Consumo se calculan siempre contra este piso fijo, no contra el
# límite de corte de cada SIM.
STANDARD_SIM_LIMIT_MB = 5 * 1024

app = FastAPI(
    title="Nagios CSL Dashboard API",
    description="Estado de las CSL cruzado con el inventario de AnyDesk y clasificación de patrones.",
    version="0.1.0",
)


# Rutas accesibles sin sesión: la pantalla de login, lo que necesita para
# dibujarse, y el health check (lo usa el watchdog, que no tiene sesión).
_RUTAS_PUBLICAS = {"/login", "/api/login", "/health", "/favicon.ico"}


def _es_publica(path: str) -> bool:
    return path in _RUTAS_PUBLICAS or path.startswith("/static/")


@app.middleware("http")
async def requerir_sesion(request: Request, call_next):
    """Exige sesión salvo en las rutas públicas.

    Sin esto cualquiera que llegue a la URL podría dar de baja hosts de Nagios
    o editar el inventario. La API responde 401 y las pantallas redirigen al
    login, para que el navegador muestre algo útil en vez de un error crudo.
    """
    if _es_publica(request.url.path):
        return await call_next(request)

    conn = get_connection(load_database_path())
    try:
        usuario = get_session_user(conn, request.cookies.get(SESSION_COOKIE))
    finally:
        conn.close()

    if usuario is None:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Sesión vencida o no iniciada."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    request.state.usuario = usuario
    return await call_next(request)


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "login.html"), headers=_SIN_CACHE)


@app.post("/api/login")
def login(payload: LoginIn, response: Response) -> dict:
    conn = get_connection(load_database_path())
    try:
        if not authenticate(conn, payload.username, payload.password):
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
        token = create_session(conn, payload.username)
    finally:
        conn.close()

    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,          # no accesible desde JavaScript
        samesite="lax",
        secure=load_cookie_secure(),  # exigir HTTPS cuando esté detrás del túnel
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )
    return {"status": "ok", "username": payload.username.strip().lower()}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    conn = get_connection(load_database_path())
    try:
        delete_session(conn, request.cookies.get(SESSION_COOKIE))
    finally:
        conn.close()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/me")
def me(request: Request) -> dict:
    return {"username": getattr(request.state, "usuario", None)}


MIN_PASSWORD_LEN = 8


@app.post("/api/change-password")
def change_password(request: Request, payload: ChangePasswordIn, response: Response) -> dict:
    """Cambia la contraseña del usuario de la sesión actual.

    Se pide la contraseña vigente aunque ya haya sesión iniciada: evita que
    alguien que encuentre la pantalla abierta se apropie de la cuenta.

    Cambiar la contraseña cierra las sesiones abiertas (es lo que se espera
    si sospechás que alguien más entró), así que se emite una nueva para la
    persona que hizo el cambio y no se la echa de su propia pantalla.
    """
    usuario = getattr(request.state, "usuario", None)
    if usuario is None:
        raise HTTPException(status_code=401, detail="Sesión vencida o no iniciada.")

    nueva = payload.new_password
    if len(nueva) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"La contraseña nueva tiene que tener al menos {MIN_PASSWORD_LEN} caracteres.",
        )
    if nueva == payload.current_password:
        raise HTTPException(status_code=422, detail="La contraseña nueva tiene que ser distinta de la actual.")

    conn = get_connection(load_database_path())
    try:
        if not authenticate(conn, usuario, payload.current_password):
            # 422 y no 401 a propósito: la sesión es válida, lo que está mal es
            # un campo del formulario. Con 401 el frontend lo interpretaría como
            # sesión vencida y echaría a la persona por un simple error de tipeo.
            raise HTTPException(status_code=422, detail="La contraseña actual no es correcta.")
        set_password(conn, usuario, nueva)
        token = create_session(conn, usuario)
    finally:
        conn.close()

    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="lax", secure=load_cookie_secure(),
        max_age=SESSION_TTL_SECONDS, path="/",
    )
    return {"status": "ok"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta", response_model=MetaOut)
def meta() -> MetaOut:
    m = get_meta()
    return MetaOut(
        last_polled_at=m.last_polled_at,
        host_count=m.host_count,
        down_count=m.down_count,
        unmatched_count=m.unmatched_count,
        last_poll_success=m.last_poll_success,
        last_poll_error=m.last_poll_error,
    )


@app.post("/api/poll", response_model=MetaOut)
def trigger_poll() -> MetaOut:
    """Dispara un poll inmediato a Nagios en vez de esperar al próximo ciclo del scheduler."""
    run_poll_job()
    m = get_meta()
    return MetaOut(
        last_polled_at=m.last_polled_at,
        host_count=m.host_count,
        down_count=m.down_count,
        unmatched_count=m.unmatched_count,
        last_poll_success=m.last_poll_success,
        last_poll_error=m.last_poll_error,
    )


@app.get("/api/hosts", response_model=list[HostStatusOut])
def hosts() -> list[HostStatusOut]:
    try:
        views = get_current_hosts()
    except InventoryUnavailableError as exc:
        logger.error("Inventario no disponible: %s", exc)
        raise HTTPException(status_code=503, detail=f"Inventario de AnyDesk no disponible: {exc}") from exc

    return [
        HostStatusOut(
            host_name=v.host_name,
            current_state=v.current_state.name,
            downtime_seconds=v.downtime_seconds,
            downtime_human=v.downtime_human,
            plugin_output=v.plugin_output,
            last_check=v.last_check,
            polled_at=v.polled_at,
            pattern=v.pattern.value,
            match_type=v.match_type.value,
            nombre_pc=v.entry.nombre_pc if v.entry else None,
            ambiente=v.entry.cliente if v.entry else None,
            anydesk_id=v.entry.anydesk_id if v.entry else None,
            rak=v.entry.rak if v.entry else None,
            crd_host_id=v.entry.crd_host_id if v.entry else None,
            notas=v.entry.notas if v.entry else None,
            sim=v.entry.sim if v.entry else None,
            # Nagios manda: reporta el operador en vivo. El inventario queda de
            # respaldo para los equipos que no exponen el dato (sin interfaz WWAN).
            empresa=v.operador_nagios or (v.entry.empresa if v.entry else None),
            estado=v.entry.estado.value if v.entry else None,
            consumo_mb=v.consumo_mb,
            limite_mb=v.limite_mb,
            consumo_pct=round(v.consumo_mb / STANDARD_SIM_LIMIT_MB * 100, 1) if v.consumo_mb is not None else None,
            consumo_updated_at=v.consumo_updated_at,
            tags=[TagOut(id=t.id, name=t.name, color=t.color, category=t.category) for t in v.tags],
            services=[
                ServiceStatusOut(
                    service_description=s.service_description,
                    current_state=s.current_state.name,
                    last_check=s.last_check,
                    last_state_change=s.last_state_change,
                    plugin_output=s.plugin_output,
                )
                for s in v.services
            ],
        )
        for v in views
    ]


@app.get("/api/insights", response_model=InsightsOut)
def insights() -> InsightsOut:
    i = get_insights()
    return InsightsOut(
        host_up=i.host_up,
        host_down=i.host_down,
        host_unreachable=i.host_unreachable,
        service_ok=i.service_ok,
        service_warning=i.service_warning,
        service_critical=i.service_critical,
        service_unknown=i.service_unknown,
    )


@app.patch("/api/hosts/{host_name}")
def edit_host_endpoint(host_name: str, payload: HostEditIn) -> dict:
    """Edita Nombre PC/Notas/SIM/Empresa/Ambiente del host en el inventario. El RAK nunca es editable desde acá."""
    if (
        payload.nombre_pc is None and payload.notas is None and payload.sim is None
        and payload.empresa is None and payload.ambiente is None and payload.estado is None
    ):
        raise HTTPException(status_code=422, detail="Nada para editar: mandá al menos un campo.")

    try:
        edit_host_inventory(
            host_name, nombre_pc=payload.nombre_pc, notas=payload.notas,
            sim=payload.sim, empresa=payload.empresa, ambiente=payload.ambiente,
            estado=payload.estado,
        )
    except HostNotEditableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InventoryWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"status": "ok"}


@app.post("/api/hosts/{host_name}/replace", response_model=HostReplaceOut)
def replace_host_endpoint(host_name: str, payload: HostReplaceIn) -> HostReplaceOut:
    """Reemplazo completo del host — Nombre, RAK, ID AnyDesk, ID CRD. Única vía que toca el RAK.

    Cuando el RAK cambia, el cambio también se aplica en Nagios (baja del
    viejo + alta del nuevo + reload). Si Nagios falla, el inventario se
    revierte solo, así que un error acá deja todo como estaba.
    """
    try:
        result = replace_host_inventory(
            host_name,
            nombre_pc=payload.nombre_pc,
            rak=payload.rak,
            anydesk_id=payload.anydesk_id,
            crd_host_id=payload.crd_host_id,
            cliente=payload.cliente,
        )
    except HostNotEditableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRakError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosReloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NagiosSSHError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InventoryWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return HostReplaceOut(
        old_rak=result.old_rak,
        old_host_name=result.old_host_name,
        new_host_name=result.new_host_name,
        nagios_applied=result.nagios_applied,
        old_removed=result.old_removed,
    )


@app.post("/api/nagios-uploader/register", response_model=HostRegisterOut)
def register_host_endpoint(payload: HostRegisterIn) -> HostRegisterOut:
    """Alta completa de un equipo: fila en el inventario (si el RAK es nuevo) + .cfg en Nagios."""
    try:
        result = register_host(
            rak=payload.rak,
            nombre_pc=payload.nombre_pc,
            cliente=payload.cliente,
            anydesk_id=payload.anydesk_id,
            crd_host_id=payload.crd_host_id,
            notas=payload.notas,
        )
    except MissingInventoryDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidRakError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosReloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NagiosSSHError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InventoryWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return HostRegisterOut(
        host_name=result.host_name,
        remote_path=result.remote_path,
        inventory_row_created=result.inventory_row_created,
        validation_output=result.validation_output,
    )


def _replacement_out(r) -> ReplacementOut:
    return ReplacementOut(
        id=r.id, old_rak=r.old_rak, new_rak=r.new_rak,
        old_host_name=r.old_host_name, new_host_name=r.new_host_name,
        nombre_pc=r.nombre_pc, ambiente=r.ambiente, replaced_at=r.replaced_at,
    )


@app.get("/api/replacements", response_model=list[ReplacementOut])
def replacements() -> list[ReplacementOut]:
    """Todos los reemplazos de equipo registrados, del más reciente al más viejo."""
    return [_replacement_out(r) for r in get_replacements()]


@app.get("/api/hosts/{host_name}/replacements", response_model=list[ReplacementOut])
def host_replacements(host_name: str) -> list[ReplacementOut]:
    """Cadena de equipos que precedieron al actual en este sitio."""
    return [_replacement_out(r) for r in get_host_replacements(host_name)]


@app.get("/api/hosts/{host_name}/nagios-info", response_model=HostNagiosInfoOut)
def host_nagios_info(host_name: str) -> HostNagiosInfoOut:
    """Cuándo se dio de alta el host en Nagios (fecha del .cfg) y desde cuándo lo ve el panel.

    Endpoint aparte del detalle porque implica una consulta SSH al servidor:
    así la vista de detalle abre al instante con lo que ya está cargado y
    completa esta parte después.
    """
    info = get_host_nagios_info(host_name)
    return HostNagiosInfoOut(
        host_name=info.host_name,
        cfg_exists=info.cfg_exists,
        cfg_modified_at=info.cfg_modified_at,
        first_seen_at=info.first_seen_at,
        ssh_error=info.ssh_error,
    )


@app.get("/api/hosts/{host_name}/history", response_model=list[SnapshotOut])
def host_history(host_name: str, days: int = 7) -> list[SnapshotOut]:
    since = int(time.time()) - days * 86400
    history = get_host_history_view(host_name, since=since)
    if not history:
        raise HTTPException(status_code=404, detail=f"No hay historial para el host '{host_name}'.")

    return [
        SnapshotOut(
            current_state=s.current_state.name,
            last_check=s.last_check,
            last_state_change=s.last_state_change,
            plugin_output=s.plugin_output,
            polled_at=s.polled_at,
        )
        for s in history
    ]


@app.get("/api/inventory/duplicates", response_model=list[DuplicateGroupOut])
def inventory_duplicates() -> list[DuplicateGroupOut]:
    try:
        groups = get_inventory_duplicate_groups()
    except InventoryUnavailableError as exc:
        logger.error("Inventario no disponible: %s", exc)
        raise HTTPException(status_code=503, detail=f"Inventario de AnyDesk no disponible: {exc}") from exc

    return [
        DuplicateGroupOut(
            anydesk_id=group.anydesk_id,
            entries=[
                DuplicateEntryOut(
                    row_number=e.row_number,
                    cliente=e.cliente,
                    nombre_pc=e.nombre_pc,
                )
                for e in group.entries
            ],
        )
        for group in groups
    ]


@app.get("/api/inventory/pending", response_model=list[PendingInventoryHostOut])
def inventory_pending() -> list[PendingInventoryHostOut]:
    """Filas del inventario que nunca reportaron a Nagios (ej. equipos en stock) —
    para poder verlas y taggearlas (RMA/Testing) aunque no exista snapshot todavía."""
    try:
        pending = get_pending_inventory_hosts()
    except InventoryUnavailableError as exc:
        logger.error("Inventario no disponible: %s", exc)
        raise HTTPException(status_code=503, detail=f"Inventario de AnyDesk no disponible: {exc}") from exc

    return [
        PendingInventoryHostOut(
            host_name=p.host_name, nombre_pc=p.nombre_pc, ambiente=p.ambiente, rak=p.rak,
            estado=p.estado,
            tags=[TagOut(id=t.id, name=t.name, color=t.color, category=t.category) for t in p.tags],
        )
        for p in pending
    ]


@app.post("/api/inventory/entries", status_code=201)
def create_inventory_entry_endpoint(payload: InventoryEntryCreateIn) -> dict:
    """Alta de un equipo nuevo en el inventario (ej. resolver un host de Inconsistencias sin fila propia)."""
    if not payload.nombre_pc.strip() or not payload.rak.strip():
        raise HTTPException(status_code=422, detail="Nombre PC y RAK son obligatorios.")
    try:
        create_inventory_entry(
            nombre_pc=payload.nombre_pc,
            rak=payload.rak,
            cliente=payload.cliente,
            anydesk_id=payload.anydesk_id,
            crd_host_id=payload.crd_host_id,
            notas=payload.notas,
        )
    except InventoryWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"status": "ok"}


@app.post("/api/nagios-uploader/prepare-add", response_model=NagiosAddPlanOut)
def prepare_nagios_add(payload: NagiosAddPlanIn) -> NagiosAddPlanOut:
    """Genera el contenido del .cfg a crear para dar de alta un RAK en Nagios.

    Nunca escribe en el servidor: solo devuelve el texto exacto para que
    la persona lo suba y aplique manualmente por SSH.
    """
    try:
        plan = prepare_add_host(payload.rak)
    except InvalidRakError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return NagiosAddPlanOut(
        host_name=plan.host_name, filename=plan.filename, remote_path=plan.remote_path, content=plan.content,
    )


@app.post("/api/nagios-uploader/prepare-remove", response_model=NagiosRemovePlanOut)
def prepare_nagios_remove(payload: NagiosRemovePlanIn) -> NagiosRemovePlanOut:
    """Lee (solo lectura) el .cfg actual del host como respaldo antes de una baja.

    Nunca borra nada en el servidor: la persona ejecuta la baja manualmente por SSH.
    """
    try:
        plan = prepare_remove_host(payload.host_name)
    except NagiosSSHError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return NagiosRemovePlanOut(
        host_name=plan.host_name,
        filename=plan.filename,
        remote_path=plan.remote_path,
        current_content=plan.current_content,
        existed=plan.current_content is not None,
    )


@app.post("/api/nagios-uploader/execute-add", response_model=NagiosExecuteResultOut)
def execute_nagios_add(payload: NagiosAddPlanIn) -> NagiosExecuteResultOut:
    """Da de alta el RAK en Nagios de verdad: escribe el .cfg, valida y recarga el servicio.

    Pensado para llamarse después de que la persona ya vio el preview de
    `/prepare-add` y confirmó — acá es donde se toca el servidor de verdad.
    """
    try:
        result = execute_add_host(payload.rak)
    except InvalidRakError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosReloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NagiosSSHError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return NagiosExecuteResultOut(
        host_name=result.host_name, filename=result.filename,
        remote_path=result.remote_path, validation_output=result.validation_output,
    )


@app.post("/api/nagios-uploader/execute-remove", response_model=NagiosExecuteResultOut)
def execute_nagios_remove(payload: NagiosRemovePlanIn) -> NagiosExecuteResultOut:
    """Da de baja el host de Nagios de verdad: borra el .cfg, valida y recarga el servicio.

    Pensado para llamarse después de que la persona ya vio el preview de
    `/prepare-remove` (con el contenido actual como respaldo) y confirmó.
    """
    try:
        result = execute_remove_host(payload.host_name)
    except NagiosHostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NagiosValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NagiosReloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NagiosSSHError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return NagiosExecuteResultOut(
        host_name=result.host_name, filename=result.filename,
        remote_path=result.remote_path, validation_output=result.validation_output,
    )


@app.get("/api/reports/html")
def download_html_report() -> Response:
    """Genera un informe HTML de estado general, con fecha/hora de emisión, para descargar y archivar."""
    try:
        content = build_html_report()
    except InventoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"Inventario de AnyDesk no disponible: {exc}") from exc

    filename = f"informe-nagios-{datetime.now().strftime('%Y-%m-%d_%H%M')}.html"
    return Response(
        content=content,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/tags", response_model=list[TagOut])
def tags() -> list[TagOut]:
    return [TagOut(id=t.id, name=t.name, color=t.color, category=t.category) for t in get_tags()]


@app.post("/api/tags", response_model=TagOut, status_code=201)
def create_tag_endpoint(payload: TagCreateIn) -> TagOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre del tag no puede estar vacío.")
    try:
        tag = create_new_tag(name, payload.color, payload.category)
    except DuplicateTagNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TagOut(id=tag.id, name=tag.name, color=tag.color, category=tag.category)


@app.patch("/api/tags/{tag_id}")
def update_tag_category_endpoint(tag_id: int, payload: TagCategoryIn) -> dict:
    """Cambia la categoría de un tag existente (Normal/RMA/Testing) sin recrearlo."""
    update_existing_tag_category(tag_id, payload.category)
    return {"status": "ok"}


@app.delete("/api/tags/{tag_id}", status_code=204)
def delete_tag_endpoint(tag_id: int) -> None:
    delete_existing_tag(tag_id)


@app.post("/api/tags/{tag_id}/assign")
def assign_tag_endpoint(tag_id: int, payload: TagHostsIn) -> dict:
    assign_tag_to_hosts(tag_id, payload.host_names)
    return {"status": "ok"}


@app.post("/api/tags/{tag_id}/unassign")
def unassign_tag_endpoint(tag_id: int, payload: TagHostsIn) -> dict:
    unassign_tag_from_hosts(tag_id, payload.host_names)
    return {"status": "ok"}


# --- Alarmas por equipo ---

def _alarma_out(a) -> AlarmaOut:
    return AlarmaOut(
        host_name=a.host_name, nombre_pc=a.nombre_pc, ambiente=a.ambiente,
        tipo=a.tipo, etiqueta=a.etiqueta, estado=int(a.estado),
        plugin_output=a.plugin_output, desde=a.desde,
        duracion_segundos=a.duracion_segundos, duracion_human=a.duracion_human,
        requiere_intervencion=a.requiere_intervencion,
    )


@app.get("/api/alarmas", response_model=AlarmasOut)
def alarmas_endpoint() -> AlarmasOut:
    """Alarmas activas agrupadas por tipo, más la bandeja de intervención.

    Un check en OK no es una alarma y no se devuelve: la pestaña muestra
    problemas, no confirmaciones de que todo anda bien.
    """
    try:
        hosts = get_current_hosts()
    except InventoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    alarmas = get_alarmas(hosts)
    return AlarmasOut(
        alarmas=[_alarma_out(a) for a in alarmas],
        resumen=[
            ResumenTipoOut(
                tipo=r.tipo, etiqueta=r.etiqueta, critical=r.critical, warning=r.warning,
                unknown=r.unknown, requieren_intervencion=r.requieren_intervencion, total=r.total,
            )
            for r in resumir_por_tipo(alarmas)
        ],
        intervenciones=[_alarma_out(a) for a in alarmas if a.requiere_intervencion],
        agentes_mudos=[
            AgenteMudoOut(
                host_name=h.host_name,
                nombre_pc=h.entry.nombre_pc if h.entry else None,
                ambiente=h.entry.cliente if h.entry else None,
                estado_host=h.current_state.name,
            )
            for h in detectar_agentes_mudos(hosts)
        ],
        tipos=[
            TipoAlarmaOut(
                nombre=t.nombre, etiqueta=t.etiqueta, descripcion=t.descripcion,
                umbral_warning=t.umbral_warning, umbral_critical=t.umbral_critical,
            )
            for t in TIPOS_ALARMA
        ],
    )


@app.get("/api/hosts/{host_name}/eventos", response_model=list[ServiceEventOut])
def eventos_de_host_endpoint(host_name: str, limit: int = 100) -> list[ServiceEventOut]:
    """Línea de tiempo de alarmas de un equipo, de lo más nuevo a lo más viejo.

    Empieza vacía: el historial se acumula desde que se desplegó la tabla
    `service_events`, no se puede reconstruir hacia atrás.
    """
    conn = get_connection(load_database_path())
    try:
        eventos = get_service_events(conn, host_name=host_name, limit=limit)
    finally:
        conn.close()

    return [
        ServiceEventOut(
            host_name=e.host_name, service_description=e.service_description,
            etiqueta=etiqueta_de(e.service_description),
            previous_state=int(e.previous_state) if e.previous_state is not None else None,
            current_state=int(e.current_state), changed_at=e.changed_at,
            plugin_output=e.plugin_output,
        )
        for e in eventos
    ]


# --- Estado operativo del inventario ---

@app.get("/api/estados", response_model=list[EstadoOpcionOut])
def estados_endpoint() -> list[EstadoOpcionOut]:
    """Los estados posibles, para poblar los desplegables sin hardcodearlos."""
    return [EstadoOpcionOut(valor=e.value, etiqueta=e.etiqueta) for e in EstadoOperativo]


@app.patch("/api/inventory/{rak}/estado")
def set_estado_endpoint(rak: str, payload: EstadoIn) -> dict:
    """Cambia el estado operativo de un equipo (Producción, Testing, RMA, Refurbish, Baja)."""
    try:
        set_inventory_estado(rak, payload.estado)
    except InventoryRowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InventoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InventoryWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok"}


@app.delete("/api/inventory/{rak}")
def delete_inventory_endpoint(rak: str) -> dict:
    """Borra del inventario un equipo que ya no existe.

    Se niega (409) si el equipo sigue dado de alta en Nagios: borrarlo dejaría
    un host sin ficha, que es la inconsistencia que esto existe para evitar.
    """
    try:
        nombre = delete_inventory_entry(rak)
    except InventoryRowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HostActivoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InventoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InventoryWriteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "nombre_pc": nombre}


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

# El HTML decide qué scripts pedir: si se cachea, el navegador sigue pidiendo
# los de la versión anterior aunque los archivos nuevos ya estén en el servidor.
_SIN_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

class _EstaticosRevalidados(StaticFiles):
    """Sirve los estáticos pidiendo al navegador que revalide siempre.

    Sin esto el navegador se queda con el `main.js` que bajó la primera vez y
    lo sigue usando contra un `index.html` nuevo. Esa mezcla no da un error
    visible: el JS viejo busca elementos que la página nueva ya no tiene,
    falla a mitad del render y deja la pantalla a medio dibujar o vacía
    (pasó con las tablas de STOCK al rediseñar la pestaña).

    `no-cache` no significa "no guardes": el navegador guarda igual, pero
    pregunta antes de usar. Si el archivo no cambió, el servidor responde 304
    sin cuerpo, así que el costo es un viaje mínimo y a cambio nadie vuelve a
    quedarse con una versión vieja después de un despliegue.
    """

    def file_response(self, *args, **kwargs):
        respuesta = super().file_response(*args, **kwargs)
        respuesta.headers["Cache-Control"] = "no-cache, must-revalidate"
        return respuesta


app.mount("/static", _EstaticosRevalidados(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), headers=_SIN_CACHE)
