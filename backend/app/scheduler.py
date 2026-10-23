"""Scheduler de polling periódico contra Nagios (APScheduler)."""

from __future__ import annotations

import datetime
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import (
    load_database_path,
    load_inventory_csv_path,
    load_kite_config,
    load_kite_consumption_schedule,
    load_nagios_ssh_config,
    load_report_schedule,
    load_smtp_config,
    load_teams_report_schedule,
    load_teams_webhook_url,
)
from app.db import get_connection, record_poll_attempt, save_snapshot, upsert_service_statuses, upsert_sim_consumption
from app.inventory_loader import load_inventory
from app.kite_client import KiteScrapeError, KiteSessionExpiredError, fetch_sim_usage
from app.mailer import ReportSendError, send_report_email
from app.report import build_report_text
from app.ssh_client import NagiosSSHError, fetch_status_dat
from app.status_parser import parse_service_statuses, parse_status_dat
from app.teams_card import build_daily_report_card
from app.teams_notifier import TeamsSendError, send_teams_card

logger = logging.getLogger(__name__)


def run_poll_job() -> None:
    """Un ciclo de polling: conectar, parsear y guardar snapshot.

    Cualquier error de conexión o configuración se loguea y se registra
    en poll_attempts, pero nunca se propaga: un fallo de red no debe
    tumbar el scheduler ni impedir el próximo intento programado. La
    herramienta debe seguir sirviendo el último dato conocido (ver
    dashboard_service) en vez de caerse.
    """
    attempted_at = int(time.time())

    try:
        ssh_config = load_nagios_ssh_config()
        db_path = load_database_path()
    except RuntimeError as exc:
        logger.error("Poll fallido (configuración inválida): %s", exc)
        return

    try:
        raw = fetch_status_dat(ssh_config)
        hosts = parse_status_dat(raw)
        services = parse_service_statuses(raw)
    except NagiosSSHError as exc:
        logger.error("Poll fallido: %s", exc)
        conn = get_connection(db_path)
        try:
            record_poll_attempt(conn, attempted_at, success=False, error_message=str(exc))
        finally:
            conn.close()
        return

    conn = get_connection(db_path)
    try:
        save_snapshot(conn, hosts, attempted_at)
        upsert_service_statuses(conn, services, attempted_at)
        record_poll_attempt(conn, attempted_at, success=True, host_count=len(hosts))
    finally:
        conn.close()

    logger.info("Poll exitoso: %d host(s), %d servicio(s) guardados.", len(hosts), len(services))


def run_report_job(kind: str) -> None:
    """Genera y envía el reporte 'daily' o 'weekly'.

    Si falta la configuración SMTP (ej. todavía no se generó la
    contraseña de aplicación), se omite con un warning en vez de
    fallar — no debe tumbar el resto del scheduler.
    """
    smtp = load_smtp_config()
    if smtp is None:
        logger.warning(
            "Reporte '%s' omitido: falta configuración SMTP en .env (ver .env.example).", kind
        )
        return

    subject, body = build_report_text(kind)
    try:
        send_report_email(smtp, subject, body)
    except ReportSendError as exc:
        logger.error(str(exc))
        return

    logger.info("Reporte '%s' enviado correctamente.", kind)


def run_teams_report_job() -> None:
    """Genera y envía el reporte diario a Teams (vía el webhook de Power Automate).

    Si falta TEAMS_WEBHOOK_URL en .env, se omite con un warning en vez
    de fallar — mismo criterio que el reporte por mail.
    """
    webhook_url = load_teams_webhook_url()
    if webhook_url is None:
        logger.warning("Reporte a Teams omitido: falta TEAMS_WEBHOOK_URL en .env (ver .env.example).")
        return

    card = build_daily_report_card()
    try:
        send_teams_card(webhook_url, card)
    except TeamsSendError as exc:
        logger.error(str(exc))
        return

    logger.info("Reporte a Teams enviado correctamente.")


def run_kite_consumption_job() -> None:
    """Scrapea el consumo mensual de cada SIM Movistar registrada en el inventario.

    Se omite (con warning) si falta la configuración de Kite en .env o si
    la sesión guardada venció — no debe tumbar el resto del scheduler. Si
    la sesión venció, hay que correr kite_setup_session.py de nuevo a mano
    (login con 2FA por mail, no se puede automatizar del todo).
    """
    cfg = load_kite_config()
    if cfg is None:
        logger.warning("Consumo de Kite omitido: falta configuración de Kite en .env (ver .env.example).")
        return

    try:
        inventory = load_inventory(load_inventory_csv_path())
    except (FileNotFoundError, RuntimeError) as exc:
        logger.error("Consumo de Kite omitido: no se pudo cargar el inventario: %s", exc)
        return

    iccs = sorted({entry.sim.strip() for entry in inventory if entry.sim.strip()})
    if not iccs:
        logger.info("Consumo de Kite omitido: no hay SIMs cargadas en el inventario.")
        return

    try:
        usages = fetch_sim_usage(cfg, iccs)
    except KiteSessionExpiredError as exc:
        logger.warning(
            "Consumo de Kite omitido: la sesión venció (%s). Correr kite_setup_session.py de nuevo a mano.", exc
        )
        return
    except KiteScrapeError as exc:
        logger.error("Consumo de Kite fallido: %s", exc)
        return

    if not usages:
        logger.warning("Consumo de Kite: no se pudo leer ninguna SIM de las %d cargadas.", len(iccs))
        return

    conn = get_connection(load_database_path())
    try:
        upsert_sim_consumption(conn, usages, int(time.time()))
    finally:
        conn.close()

    logger.info("Consumo de Kite actualizado: %d de %d SIM(s).", len(usages), len(iccs))


def build_scheduler(interval_minutes: int) -> BackgroundScheduler:
    """Arma el scheduler: poll inmediato + repeticiones, y los reportes por mail programados."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_poll_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="nagios_poll",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.datetime.now(),
    )

    report_schedule = load_report_schedule()
    scheduler.add_job(
        run_report_job,
        args=["daily"],
        trigger=CronTrigger(hour=report_schedule.daily_hour, minute=report_schedule.daily_minute),
        id="daily_report",
        max_instances=1,
    )
    scheduler.add_job(
        run_report_job,
        args=["weekly"],
        trigger=CronTrigger(
            day_of_week=report_schedule.weekly_day,
            hour=report_schedule.weekly_hour,
            minute=report_schedule.weekly_minute,
        ),
        id="weekly_report",
        max_instances=1,
    )

    teams_schedule = load_teams_report_schedule()
    scheduler.add_job(
        run_teams_report_job,
        trigger=CronTrigger(hour=teams_schedule.morning_hour, minute=teams_schedule.morning_minute),
        id="teams_report_morning",
        max_instances=1,
    )
    scheduler.add_job(
        run_teams_report_job,
        trigger=CronTrigger(hour=teams_schedule.afternoon_hour, minute=teams_schedule.afternoon_minute),
        id="teams_report_afternoon",
        max_instances=1,
    )

    # El scrapeo diario de consumo de Kite queda sin programar: la columna de
    # consumo se sacó de la interfaz porque no resolvía lo que el usuario
    # necesitaba, y sostener un scrapeo de ~8 minutos contra Kite (con su
    # sesión que hay que renovar a mano) para un dato que nadie mira es puro
    # costo. El código queda intacto: para reactivarlo, volver a registrar
    # `run_kite_consumption_job` acá.

    return scheduler
