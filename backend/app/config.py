"""Carga de configuración desde .env (raíz del proyecto)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class NagiosSSHConfig:
    host: str
    port: int
    user: str
    key_path: str
    status_dat_path: str
    hosts_cfg_path: str
    binary_path: str = "/usr/local/nagios/bin/nagios"
    main_cfg_path: str = "/usr/local/nagios/etc/nagios.cfg"
    reload_command: str = "sudo systemctl reload nagios"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    recipients: list[str]


@dataclass(frozen=True)
class ReportScheduleConfig:
    daily_hour: int
    daily_minute: int
    weekly_day: str
    weekly_hour: int
    weekly_minute: int


@dataclass(frozen=True)
class TeamsReportScheduleConfig:
    morning_hour: int
    morning_minute: int
    afternoon_hour: int
    afternoon_minute: int


@dataclass(frozen=True)
class KiteConfig:
    login_url: str
    username: str
    password: str


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno {name}. Revisá tu archivo .env "
            f"(ver .env.example para la lista completa)."
        )
    return value


def load_inventory_csv_path() -> str:
    """Resuelve INVENTORY_CSV_PATH contra la raíz del proyecto si es relativa."""
    raw = _require("INVENTORY_CSV_PATH")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def load_poll_interval_minutes() -> int:
    return int(os.getenv("POLL_INTERVAL_MINUTES", "10"))


def load_database_path() -> str:
    """Resuelve DATABASE_PATH contra la raíz del proyecto si es relativa."""
    raw = os.getenv("DATABASE_PATH", "./data/dashboard.db")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def load_smtp_config() -> SmtpConfig | None:
    """None si falta cualquier dato requerido: los reportes se omiten (con warning) en vez de fallar."""
    host = os.getenv("SMTP_HOST", "")
    port_raw = os.getenv("SMTP_PORT", "")
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("SMTP_FROM", "") or user
    recipients = [r.strip() for r in os.getenv("REPORT_RECIPIENTS", "").split(",") if r.strip()]

    if not (host and port_raw and user and password and recipients):
        return None

    return SmtpConfig(host=host, port=int(port_raw), user=user, password=password, from_addr=from_addr, recipients=recipients)


def load_report_schedule() -> ReportScheduleConfig:
    return ReportScheduleConfig(
        daily_hour=int(os.getenv("REPORT_DAILY_HOUR", "8")),
        daily_minute=int(os.getenv("REPORT_DAILY_MINUTE", "0")),
        weekly_day=os.getenv("REPORT_WEEKLY_DAY", "mon"),
        weekly_hour=int(os.getenv("REPORT_WEEKLY_HOUR", "8")),
        weekly_minute=int(os.getenv("REPORT_WEEKLY_MINUTE", "0")),
    )


def load_teams_webhook_url() -> str | None:
    """None si TEAMS_WEBHOOK_URL no está configurada: el reporte a Teams se omite (con warning)."""
    return os.getenv("TEAMS_WEBHOOK_URL", "").strip() or None


def load_teams_report_schedule() -> TeamsReportScheduleConfig:
    return TeamsReportScheduleConfig(
        morning_hour=int(os.getenv("TEAMS_REPORT_MORNING_HOUR", "9")),
        morning_minute=int(os.getenv("TEAMS_REPORT_MORNING_MINUTE", "0")),
        afternoon_hour=int(os.getenv("TEAMS_REPORT_AFTERNOON_HOUR", "16")),
        afternoon_minute=int(os.getenv("TEAMS_REPORT_AFTERNOON_MINUTE", "30")),
    )


def load_kite_config() -> KiteConfig | None:
    """None si falta cualquier dato requerido: el consumo Kite se omite (con warning) en vez de fallar."""
    login_url = os.getenv("KITE_LOGIN_URL", "").strip()
    username = os.getenv("KITE_USERNAME", "").strip()
    password = os.getenv("KITE_PASSWORD", "")

    if not (login_url and username and password):
        return None

    return KiteConfig(login_url=login_url, username=username, password=password)


def load_kite_consumption_schedule() -> tuple[int, int]:
    """Hora del scrape diario de consumo de Kite. Una vez por día, a pedido del usuario."""
    return (
        int(os.getenv("KITE_CONSUMPTION_HOUR", "7")),
        int(os.getenv("KITE_CONSUMPTION_MINUTE", "0")),
    )


def load_nagios_ssh_config() -> NagiosSSHConfig:
    return NagiosSSHConfig(
        host=_require("NAGIOS_SSH_HOST"),
        port=int(os.getenv("NAGIOS_SSH_PORT", "22")),
        user=_require("NAGIOS_SSH_USER"),
        key_path=_require("NAGIOS_SSH_KEY_PATH"),
        status_dat_path=os.getenv(
            "NAGIOS_STATUS_DAT_PATH", "/usr/local/nagios/var/status.dat"
        ),
        hosts_cfg_path=os.getenv(
            "NAGIOS_HOSTS_CFG_PATH", "/usr/local/nagios/etc/objects/hosts/"
        ),
        binary_path=os.getenv("NAGIOS_BIN_PATH", "/usr/local/nagios/bin/nagios"),
        main_cfg_path=os.getenv("NAGIOS_MAIN_CFG_PATH", "/usr/local/nagios/etc/nagios.cfg"),
        reload_command=os.getenv("NAGIOS_RELOAD_COMMAND", "sudo systemctl reload nagios"),
    )


def load_cookie_secure() -> bool:
    """Si la cookie de sesión exige HTTPS.

    En la red local el panel se sirve por http, así que por defecto queda en
    false; detrás del túnel público (que es https) hay que ponerlo en true,
    o el navegador descarta la cookie y nadie puede entrar.
    """
    return os.getenv("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes", "si")
