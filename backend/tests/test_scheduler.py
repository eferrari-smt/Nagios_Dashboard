from app import scheduler
from app.config import KiteConfig, NagiosSSHConfig, SmtpConfig
from app.db import get_all_sim_consumption, get_connection, get_host_history, get_last_poll_attempt
from app.inventory_loader import InventoryEntry
from app.kite_client import KiteSessionExpiredError, SimUsage
from app.mailer import ReportSendError
from app.ssh_client import NagiosSSHError
from app.teams_notifier import TeamsSendError

DUMMY_KITE_CONFIG = KiteConfig(
    login_url="https://kite.example.com/", username="dummy", password="dummy",
)

DUMMY_SMTP = SmtpConfig(
    host="smtp.gmail.com", port=587, user="bot@example.com", password="secret",
    from_addr="bot@example.com", recipients=["eferrari@example.com"],
)

DUMMY_CONFIG = NagiosSSHConfig(
    host="dummy", port=22, user="dummy", key_path="dummy",
    status_dat_path="/dummy", hosts_cfg_path="/dummy",
)

VALID_STATUS_DAT = """
hoststatus {
\thost_name=HOST1
\tcurrent_state=1
\tlast_check=1000
\tlast_state_change=900
\tplugin_output=DOWN
\t}
"""


def test_run_poll_job_saves_snapshot_and_records_success(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(scheduler, "load_nagios_ssh_config", lambda: DUMMY_CONFIG)
    monkeypatch.setattr(scheduler, "load_database_path", lambda: db_path)
    monkeypatch.setattr(scheduler, "fetch_status_dat", lambda config: VALID_STATUS_DAT)

    scheduler.run_poll_job()

    conn = get_connection(db_path)
    history = get_host_history(conn, "HOST1")
    attempt = get_last_poll_attempt(conn)
    conn.close()

    assert len(history) == 1
    assert attempt.success is True
    assert attempt.host_count == 1


def test_run_poll_job_records_failure_without_crashing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(scheduler, "load_nagios_ssh_config", lambda: DUMMY_CONFIG)
    monkeypatch.setattr(scheduler, "load_database_path", lambda: db_path)

    def raise_ssh_error(config):
        raise NagiosSSHError("no se pudo conectar")

    monkeypatch.setattr(scheduler, "fetch_status_dat", raise_ssh_error)

    scheduler.run_poll_job()  # no debe levantar excepción

    conn = get_connection(db_path)
    attempt = get_last_poll_attempt(conn)
    conn.close()

    assert attempt.success is False
    assert attempt.error_message == "no se pudo conectar"


def test_run_poll_job_handles_missing_config_without_crashing(monkeypatch):
    def raise_config_error():
        raise RuntimeError("falta NAGIOS_SSH_HOST")

    monkeypatch.setattr(scheduler, "load_nagios_ssh_config", raise_config_error)

    scheduler.run_poll_job()  # no debe levantar excepción, ni siquiera puede abrir la db


def test_build_scheduler_registers_job_with_correct_interval():
    sched = scheduler.build_scheduler(interval_minutes=10)
    job = sched.get_job("nagios_poll")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 600


def test_build_scheduler_registers_report_jobs():
    sched = scheduler.build_scheduler(interval_minutes=10)
    assert sched.get_job("daily_report") is not None
    assert sched.get_job("weekly_report") is not None


def test_build_scheduler_registers_teams_report_jobs():
    sched = scheduler.build_scheduler(interval_minutes=10)
    morning = sched.get_job("teams_report_morning")
    afternoon = sched.get_job("teams_report_afternoon")
    assert morning is not None
    assert afternoon is not None


def test_run_report_job_skips_when_smtp_not_configured(monkeypatch):
    monkeypatch.setattr(scheduler, "load_smtp_config", lambda: None)

    called = []
    monkeypatch.setattr(scheduler, "build_report_text", lambda kind: called.append(kind))

    scheduler.run_report_job("daily")  # no debe levantar excepción

    assert called == []  # ni siquiera arma el reporte si falta SMTP


def test_run_report_job_sends_when_configured(monkeypatch):
    monkeypatch.setattr(scheduler, "load_smtp_config", lambda: DUMMY_SMTP)
    monkeypatch.setattr(scheduler, "build_report_text", lambda kind: (f"Asunto {kind}", "Cuerpo"))

    sent = []
    monkeypatch.setattr(scheduler, "send_report_email", lambda smtp, subject, body: sent.append((smtp, subject, body)))

    scheduler.run_report_job("weekly")

    assert len(sent) == 1
    assert sent[0][1] == "Asunto weekly"


def test_run_report_job_handles_send_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(scheduler, "load_smtp_config", lambda: DUMMY_SMTP)
    monkeypatch.setattr(scheduler, "build_report_text", lambda kind: ("Asunto", "Cuerpo"))

    def raise_send_error(smtp, subject, body):
        raise ReportSendError("fallo de conexión SMTP")

    monkeypatch.setattr(scheduler, "send_report_email", raise_send_error)

    scheduler.run_report_job("daily")  # no debe levantar excepción


def test_run_teams_report_job_skips_when_webhook_not_configured(monkeypatch):
    monkeypatch.setattr(scheduler, "load_teams_webhook_url", lambda: None)

    called = []
    monkeypatch.setattr(scheduler, "build_daily_report_card", lambda: called.append("built"))

    scheduler.run_teams_report_job()  # no debe levantar excepción

    assert called == []  # ni siquiera arma la tarjeta si falta el webhook


def test_run_teams_report_job_sends_when_configured(monkeypatch):
    monkeypatch.setattr(scheduler, "load_teams_webhook_url", lambda: "https://prod.example.com/workflow/xyz")
    card = {"type": "AdaptiveCard", "body": []}
    monkeypatch.setattr(scheduler, "build_daily_report_card", lambda: card)

    sent = []
    monkeypatch.setattr(scheduler, "send_teams_card", lambda url, c: sent.append((url, c)))

    scheduler.run_teams_report_job()

    assert sent == [("https://prod.example.com/workflow/xyz", card)]


def test_run_teams_report_job_handles_send_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(scheduler, "load_teams_webhook_url", lambda: "https://prod.example.com/workflow/xyz")
    monkeypatch.setattr(scheduler, "build_daily_report_card", lambda: {"type": "AdaptiveCard", "body": []})

    def raise_send_error(url, card):
        raise TeamsSendError("fallo de conexión")

    monkeypatch.setattr(scheduler, "send_teams_card", raise_send_error)

    scheduler.run_teams_report_job()  # no debe levantar excepción


def test_build_scheduler_does_not_schedule_kite_consumption():
    """El scrapeo de Kite quedó fuera del scheduler: la columna de consumo se sacó
    de la interfaz, así que el job es costo sin uso. La función sigue existiendo
    y testeada por si se reactiva."""
    sched = scheduler.build_scheduler(interval_minutes=10)
    assert sched.get_job("kite_consumption") is None


def test_run_kite_consumption_job_skips_when_kite_not_configured(monkeypatch):
    monkeypatch.setattr(scheduler, "load_kite_config", lambda: None)

    called = []
    monkeypatch.setattr(scheduler, "load_inventory", lambda path: called.append("loaded"))

    scheduler.run_kite_consumption_job()  # no debe levantar excepción

    assert called == []  # ni siquiera carga el inventario si falta la config de Kite


def test_run_kite_consumption_job_skips_when_no_sims_in_inventory(monkeypatch):
    monkeypatch.setattr(scheduler, "load_kite_config", lambda: DUMMY_KITE_CONFIG)
    monkeypatch.setattr(scheduler, "load_inventory_csv_path", lambda: "dummy.csv")
    monkeypatch.setattr(
        scheduler, "load_inventory",
        lambda path: [InventoryEntry(nombre_pc="PC1", rak="RAK1", anydesk_id="1", cliente="GCBA", row_number=2)],
    )

    called = []
    monkeypatch.setattr(scheduler, "fetch_sim_usage", lambda cfg, iccs: called.append(iccs))

    scheduler.run_kite_consumption_job()  # no debe levantar excepción

    assert called == []  # ninguna fila tiene SIM cargada


def test_run_kite_consumption_job_saves_usage(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(scheduler, "load_kite_config", lambda: DUMMY_KITE_CONFIG)
    monkeypatch.setattr(scheduler, "load_database_path", lambda: db_path)
    monkeypatch.setattr(scheduler, "load_inventory_csv_path", lambda: "dummy.csv")
    monkeypatch.setattr(
        scheduler, "load_inventory",
        lambda path: [InventoryEntry(nombre_pc="PC1", rak="RAK1", anydesk_id="1", cliente="GCBA", row_number=2, sim="ICC1")],
    )
    monkeypatch.setattr(
        scheduler, "fetch_sim_usage",
        lambda cfg, iccs: {"ICC1": SimUsage(icc="ICC1", consumo_mb=4237.312, limite_mb=10240.0)},
    )

    scheduler.run_kite_consumption_job()

    conn = get_connection(db_path)
    rows = get_all_sim_consumption(conn)
    conn.close()

    assert rows["ICC1"].consumo_mb == 4237.312
    assert rows["ICC1"].limite_mb == 10240.0


def test_run_kite_consumption_job_handles_expired_session_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "load_kite_config", lambda: DUMMY_KITE_CONFIG)
    monkeypatch.setattr(scheduler, "load_database_path", lambda: str(tmp_path / "test.db"))
    monkeypatch.setattr(scheduler, "load_inventory_csv_path", lambda: "dummy.csv")
    monkeypatch.setattr(
        scheduler, "load_inventory",
        lambda path: [InventoryEntry(nombre_pc="PC1", rak="RAK1", anydesk_id="1", cliente="GCBA", row_number=2, sim="ICC1")],
    )

    def raise_expired(cfg, iccs):
        raise KiteSessionExpiredError("sesión vencida")

    monkeypatch.setattr(scheduler, "fetch_sim_usage", raise_expired)

    scheduler.run_kite_consumption_job()  # no debe levantar excepción
