"""Acceso SSH/SFTP al servidor Nagios: lectura de estado y, para el Nagios
Uploader, escritura de altas/bajas de host.

`fetch_status_dat` y `fetch_remote_file` son de solo lectura — se usan en
cada poll y no tienen motivo para tocar el servidor. `write_remote_file`,
`delete_remote_file` y `run_remote_command` sí escriben/ejecutan: existen
únicamente para el ciclo alta/baja de `app.nagios_uploader` (escribir/borrar
el .cfg, validar con `nagios -v`, recargar el servicio), a pedido explícito
del usuario — antes de esto la herramienta era estrictamente de solo lectura
contra este servidor.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from pathlib import Path

import paramiko

from app.config import NagiosSSHConfig

logger = logging.getLogger(__name__)


class NagiosSSHError(RuntimeError):
    """Error de conexión, lectura o escritura contra el servidor Nagios."""


@dataclass(frozen=True)
class RemoteCommandResult:
    exit_status: int
    stdout: str
    stderr: str


def _load_private_key(key_path: str) -> paramiko.PKey:
    try:
        return paramiko.RSAKey.from_private_key_file(key_path)
    except paramiko.SSHException:
        try:
            return paramiko.Ed25519Key.from_private_key_file(key_path)
        except paramiko.SSHException as exc:
            raise NagiosSSHError(
                "No se pudo leer la llave privada (formato no reconocido como RSA ni Ed25519)."
            ) from exc


def _connect(config: NagiosSSHConfig) -> paramiko.SSHClient:
    key_path = Path(config.key_path)
    if not key_path.is_file():
        raise NagiosSSHError(
            f"No se encontró la llave .pem en la ruta configurada "
            f"(NAGIOS_SSH_KEY_PATH). Verificá el archivo .env."
        )

    private_key = _load_private_key(str(key_path))

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=config.host, port=config.port, username=config.user,
            pkey=private_key, timeout=15,
        )
    except paramiko.AuthenticationException as exc:
        raise NagiosSSHError("Autenticación SSH rechazada. Verificá usuario y llave.") from exc
    except (paramiko.SSHException, OSError) as exc:
        raise NagiosSSHError(f"No se pudo conectar a {config.host}:{config.port}.") from exc
    return client


def fetch_status_dat(config: NagiosSSHConfig) -> str:
    """Conecta a la EC2 por SSH y devuelve el contenido de status.dat como texto.

    Levanta NagiosSSHError con un mensaje claro ante cualquier falla
    (llave inexistente, host inalcanzable, auth rechazada, archivo remoto
    faltante), para que el caller pueda decidir cómo degradar en vez de
    que el proceso explote con un traceback de paramiko.
    """
    client = _connect(config)
    try:
        sftp = client.open_sftp()
        try:
            with sftp.open(config.status_dat_path, "r") as remote_file:
                raw = remote_file.read()
        except FileNotFoundError as exc:
            raise NagiosSSHError(
                f"No se encontró {config.status_dat_path} en el servidor remoto."
            ) from exc
        finally:
            sftp.close()
    except paramiko.SSHException as exc:
        raise NagiosSSHError("Falló la sesión SFTP al leer status.dat.") from exc
    finally:
        client.close()

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    logger.info("status.dat leído correctamente (%d bytes)", len(raw))
    return raw


def fetch_remote_file(config: NagiosSSHConfig, remote_path: str) -> str | None:
    """Lee un archivo remoto puntual por SFTP. Devuelve None si no existe.

    Usado por el Nagios Uploader para leer el .cfg actual de un host antes
    de preparar una baja (referencia de respaldo) — sigue siendo de solo
    lectura, igual que `fetch_status_dat`.
    """
    client = _connect(config)
    try:
        sftp = client.open_sftp()
        try:
            with sftp.open(remote_path, "r") as remote_file:
                raw = remote_file.read()
        except FileNotFoundError:
            return None
        finally:
            sftp.close()
    except paramiko.SSHException as exc:
        raise NagiosSSHError(f"Falló la sesión SFTP al leer {remote_path}.") from exc
    finally:
        client.close()

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw


def stat_remote_file(config: NagiosSSHConfig, remote_path: str) -> int | None:
    """Fecha de última modificación de un archivo remoto (epoch). None si no existe.

    Se usa para saber cuándo se dio de alta un host en Nagios: la fecha del
    .cfg es el dato real del alta. La base del panel solo sabe desde cuándo
    lo viene viendo, que para los equipos anteriores a la puesta en marcha
    del panel no es lo mismo.
    """
    client = _connect(config)
    try:
        sftp = client.open_sftp()
        try:
            return int(sftp.stat(remote_path).st_mtime)
        except FileNotFoundError:
            return None
        finally:
            sftp.close()
    except paramiko.SSHException as exc:
        raise NagiosSSHError(f"Falló la consulta de {remote_path}.") from exc
    finally:
        client.close()


def write_remote_file(config: NagiosSSHConfig, remote_path: str, content: str) -> None:
    """Crea o sobreescribe un archivo remoto, con privilegios (`sudo tee`).

    Escritura real contra el servidor — usada solo por el alta de Nagios
    Uploader (`app.nagios_uploader.execute_add_host`), siempre seguida de
    una validación de config antes de recargar el servicio. No usa SFTP
    puro: el directorio de hosts.cfg no es escribible directo por el
    usuario SSH (confirmado contra el servidor real — PermissionError),
    solo por root vía sudo, igual que el reload del servicio.
    """
    client = _connect(config)
    try:
        command = f"sudo tee {shlex.quote(remote_path)} > /dev/null"
        _stdin, stdout, _stderr = client.exec_command(command, timeout=30)
        _stdin.write(content)
        _stdin.channel.shutdown_write()
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = _stderr.read().decode("utf-8", errors="replace")
            raise NagiosSSHError(f"Falló la escritura de {remote_path} (código {exit_status}): {err}")
    except (paramiko.SSHException, OSError) as exc:
        raise NagiosSSHError(f"Falló la escritura de {remote_path}.") from exc
    finally:
        client.close()


def delete_remote_file(config: NagiosSSHConfig, remote_path: str) -> None:
    """Borra un archivo remoto con privilegios (`sudo rm -f`). No falla si ya no existe.

    Usada por la baja de Nagios Uploader (`execute_remove_host`) y como
    reversión automática del alta si la validación de config falla. Ver
    `write_remote_file` sobre por qué hace falta sudo en vez de SFTP.
    """
    client = _connect(config)
    try:
        command = f"sudo rm -f {shlex.quote(remote_path)}"
        _stdin, stdout, stderr = client.exec_command(command, timeout=30)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = stderr.read().decode("utf-8", errors="replace")
            raise NagiosSSHError(f"Falló el borrado de {remote_path} (código {exit_status}): {err}")
    except (paramiko.SSHException, OSError) as exc:
        raise NagiosSSHError(f"Falló el borrado de {remote_path}.") from exc
    finally:
        client.close()


def run_remote_command(config: NagiosSSHConfig, command: str, timeout: int = 30) -> RemoteCommandResult:
    """Ejecuta un comando de shell en el servidor remoto y devuelve su resultado.

    Usada solo para validar la config de Nagios (`nagios -v`) y recargar
    el servicio (`systemctl reload nagios`) — nunca con input del usuario
    interpolado en el comando.
    """
    client = _connect(config)
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
    except (paramiko.SSHException, OSError) as exc:
        raise NagiosSSHError(f"Falló la ejecución remota: {command!r}.") from exc
    finally:
        client.close()

    return RemoteCommandResult(exit_status=exit_status, stdout=out, stderr=err)
