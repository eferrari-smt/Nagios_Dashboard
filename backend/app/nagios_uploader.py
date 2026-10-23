"""Nagios Uploader: alta/baja de RAK en el servidor Nagios.

## Dos niveles: preparar (siempre) vs. ejecutar (a pedido explícito)

`prepare_add_host` / `prepare_remove_host` solo generan el contenido
exacto del .cfg (alta) o leen el actual como respaldo (baja) — nunca
tocan el servidor, y siguen siendo el paso de previsualización antes de
cualquier alta/baja real.

`execute_add_host` / `execute_remove_host` sí escriben de verdad: hasta
2026-08-26 este proyecto tenía como regla no negociable que la
herramienta nunca escribe/modifica/borra nada en el servidor Nagios — el
usuario reemplazó explícitamente esa regla para poder reemplazar a
NagiosUploader.exe (una herramienta externa que hacía este mismo ciclo)
desde este mismo dashboard. Cada `execute_*` sigue el mismo ciclo que
usaba esa herramienta: escribir/borrar el .cfg → validar la config
completa de Nagios (`nagios -v`) → si valida, recargar el servicio; si
no valida, revertir el cambio sin tocar el servicio en producción.

## Formato real de los .cfg (confirmado leyendo el servidor)

Un archivo por host, nombrado `<host_name>.cfg`, con:

    define host {
    use windows_powershell
    host_name <HOST_NAME>
    }

Y `host_name` es el mismo string que matching.py trunca a 15
caracteres del RAK (límite NetBIOS) — por eso se reusa `normalize` y
`NAGIOS_NETBIOS_TRUNCATE_LEN` de matching.py para derivarlo del RAK
que carga el usuario.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import NagiosSSHConfig, load_nagios_ssh_config
from app.matching import NAGIOS_NETBIOS_TRUNCATE_LEN, normalize
from app.ssh_client import (
    delete_remote_file,
    fetch_remote_file,
    run_remote_command,
    stat_remote_file,
    write_remote_file,
)

_HOST_CFG_TEMPLATE = "define host {{\nuse windows_powershell\nhost_name {host_name}\n}}\n"


class InvalidRakError(ValueError):
    """El RAK ingresado no produce un host_name válido tras normalizar."""


class NagiosValidationError(RuntimeError):
    """La config de Nagios no pasó `nagios -v` después del cambio — se revirtió."""


class NagiosReloadError(RuntimeError):
    """El cambio se escribió y validó, pero no se pudo recargar el servicio Nagios."""


class NagiosHostNotFoundError(RuntimeError):
    """Se pidió dar de baja un host que no tiene .cfg en el servidor."""


def derive_host_name(rak: str) -> str:
    host_name = normalize(rak)[:NAGIOS_NETBIOS_TRUNCATE_LEN]
    if not host_name:
        raise InvalidRakError("El RAK ingresado no contiene caracteres alfanuméricos.")
    return host_name


@dataclass(frozen=True)
class AddHostPlan:
    host_name: str
    filename: str
    remote_path: str
    content: str


def prepare_add_host(rak: str, ssh_config: NagiosSSHConfig | None = None) -> AddHostPlan:
    """Genera el contenido exacto del .cfg a crear. No toca el servidor."""
    ssh_config = ssh_config or load_nagios_ssh_config()
    host_name = derive_host_name(rak)
    filename = f"{host_name}.cfg"
    remote_path = f"{ssh_config.hosts_cfg_path.rstrip('/')}/{filename}"
    content = _HOST_CFG_TEMPLATE.format(host_name=host_name)
    return AddHostPlan(host_name=host_name, filename=filename, remote_path=remote_path, content=content)


@dataclass(frozen=True)
class RemoveHostPlan:
    host_name: str
    filename: str
    remote_path: str
    current_content: str | None  # None si el archivo no existe en el servidor (referencia de respaldo)


def prepare_remove_host(host_name: str, ssh_config: NagiosSSHConfig | None = None) -> RemoveHostPlan:
    """Lee (solo lectura) el .cfg actual del host para mostrarlo como respaldo antes de la baja."""
    ssh_config = ssh_config or load_nagios_ssh_config()
    filename = f"{host_name}.cfg"
    remote_path = f"{ssh_config.hosts_cfg_path.rstrip('/')}/{filename}"
    current_content = fetch_remote_file(ssh_config, remote_path)
    return RemoveHostPlan(
        host_name=host_name, filename=filename, remote_path=remote_path, current_content=current_content,
    )


def get_host_cfg_mtime(host_name: str, ssh_config: NagiosSSHConfig | None = None) -> int | None:
    """Fecha (epoch) del .cfg del host en el servidor, o None si no tiene .cfg.

    Es el dato real de cuándo se dio de alta el host en Nagios. Solo lectura.
    """
    ssh_config = ssh_config or load_nagios_ssh_config()
    remote_path = f"{ssh_config.hosts_cfg_path.rstrip('/')}/{host_name}.cfg"
    return stat_remote_file(ssh_config, remote_path)


@dataclass(frozen=True)
class _ValidationResult:
    ok: bool
    output: str


def _validate_config(ssh_config: NagiosSSHConfig) -> _ValidationResult:
    """Corre `nagios -v <config>` en el servidor. Con sudo: algunos .cfg incluidos
    (ej. resource.cfg con credenciales) no son legibles para ec2-user directo."""
    command = f"sudo {ssh_config.binary_path} -v {ssh_config.main_cfg_path}"
    result = run_remote_command(ssh_config, command, timeout=30)
    return _ValidationResult(ok=result.exit_status == 0, output=result.stdout + result.stderr)


def _reload_nagios(ssh_config: NagiosSSHConfig) -> None:
    result = run_remote_command(ssh_config, ssh_config.reload_command, timeout=30)
    if result.exit_status != 0:
        raise NagiosReloadError(
            f"El cambio se escribió y validó, pero no se pudo recargar Nagios "
            f"(código {result.exit_status}):\n{result.stdout}{result.stderr}"
        )


@dataclass(frozen=True)
class AddHostResult:
    host_name: str
    filename: str
    remote_path: str
    validation_output: str


def execute_add_host(rak: str, ssh_config: NagiosSSHConfig | None = None) -> AddHostResult:
    """Da de alta un RAK en Nagios de verdad: escribe el .cfg, valida la config, y recarga.

    Si `nagios -v` falla después de escribir, borra el .cfg recién creado
    y nunca llega a tocar el servicio — Nagios sigue corriendo con la
    config anterior, intacta.
    """
    ssh_config = ssh_config or load_nagios_ssh_config()
    plan = prepare_add_host(rak, ssh_config=ssh_config)

    write_remote_file(ssh_config, plan.remote_path, plan.content)

    validation = _validate_config(ssh_config)
    if not validation.ok:
        delete_remote_file(ssh_config, plan.remote_path)
        raise NagiosValidationError(
            f"La config de Nagios no validó después de agregar '{plan.host_name}' — "
            f"se revirtió el alta, el servicio no se tocó.\n{validation.output}"
        )

    _reload_nagios(ssh_config)

    return AddHostResult(
        host_name=plan.host_name, filename=plan.filename, remote_path=plan.remote_path,
        validation_output=validation.output,
    )


@dataclass(frozen=True)
class RemoveHostResult:
    host_name: str
    filename: str
    remote_path: str
    validation_output: str


def execute_remove_host(host_name: str, ssh_config: NagiosSSHConfig | None = None) -> RemoveHostResult:
    """Da de baja un host de Nagios de verdad: borra el .cfg, valida la config, y recarga.

    Si `nagios -v` falla después de borrar, restaura el .cfg desde el
    contenido leído antes de borrarlo y nunca llega a tocar el servicio.
    """
    ssh_config = ssh_config or load_nagios_ssh_config()
    plan = prepare_remove_host(host_name, ssh_config=ssh_config)
    if plan.current_content is None:
        raise NagiosHostNotFoundError(
            f"'{host_name}' no tiene un .cfg en el servidor — no hay nada para borrar."
        )

    delete_remote_file(ssh_config, plan.remote_path)

    validation = _validate_config(ssh_config)
    if not validation.ok:
        write_remote_file(ssh_config, plan.remote_path, plan.current_content)
        raise NagiosValidationError(
            f"La config de Nagios no validó después de borrar '{host_name}' — "
            f"se restauró el archivo, el servicio no se tocó.\n{validation.output}"
        )

    _reload_nagios(ssh_config)

    return RemoveHostResult(
        host_name=plan.host_name, filename=plan.filename, remote_path=plan.remote_path,
        validation_output=validation.output,
    )


@dataclass(frozen=True)
class ReplaceHostResult:
    old_host_name: str | None  # None si el RAK viejo no derivaba un host_name válido
    old_removed: bool  # si el .cfg viejo existía y se borró
    new_host_name: str
    new_remote_path: str
    validation_output: str


def execute_replace_host(
    old_rak: str, new_rak: str, ssh_config: NagiosSSHConfig | None = None
) -> ReplaceHostResult:
    """Cambia el RAK de un equipo en Nagios en un solo ciclo: borra el .cfg del
    RAK viejo (si existía), escribe el del nuevo, valida UNA vez y recarga UNA vez.

    Es un ciclo propio en vez de un `execute_remove_host` + `execute_add_host`
    encadenados para que el reemplazo sea atómico: una sola validación sobre el
    estado final y un solo reload, en vez de dejar a Nagios pasar por un estado
    intermedio (sin el viejo y sin el nuevo) con su propio reload.

    Si la validación falla, restaura el estado exacto anterior (borra el nuevo,
    reescribe el viejo con su contenido original) y no toca el servicio.
    """
    ssh_config = ssh_config or load_nagios_ssh_config()

    add_plan = prepare_add_host(new_rak, ssh_config=ssh_config)

    try:
        old_host_name = derive_host_name(old_rak)
    except InvalidRakError:
        old_host_name = None

    old_plan = None
    if old_host_name and old_host_name != add_plan.host_name:
        old_plan = prepare_remove_host(old_host_name, ssh_config=ssh_config)
        if old_plan.current_content is not None:
            delete_remote_file(ssh_config, old_plan.remote_path)

    write_remote_file(ssh_config, add_plan.remote_path, add_plan.content)

    validation = _validate_config(ssh_config)
    if not validation.ok:
        delete_remote_file(ssh_config, add_plan.remote_path)
        if old_plan is not None and old_plan.current_content is not None:
            write_remote_file(ssh_config, old_plan.remote_path, old_plan.current_content)
        raise NagiosValidationError(
            f"La config de Nagios no validó después de reemplazar por '{add_plan.host_name}' — "
            f"se restauró el estado anterior, el servicio no se tocó.\n{validation.output}"
        )

    _reload_nagios(ssh_config)

    return ReplaceHostResult(
        old_host_name=old_host_name,
        old_removed=old_plan is not None and old_plan.current_content is not None,
        new_host_name=add_plan.host_name,
        new_remote_path=add_plan.remote_path,
        validation_output=validation.output,
    )
