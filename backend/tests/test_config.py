from app.config import load_report_schedule, load_smtp_config, load_teams_report_schedule, load_teams_webhook_url

SMTP_ENV = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_USER": "bot@example.com",
    "SMTP_PASSWORD": "app-password",
    "SMTP_FROM": "bot@example.com",
    "REPORT_RECIPIENTS": "eferrari@example.com, jefe@example.com",
}


def _set_env(monkeypatch, values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_load_smtp_config_returns_none_when_unset(monkeypatch):
    for key in SMTP_ENV:
        monkeypatch.delenv(key, raising=False)
    assert load_smtp_config() is None


def test_load_smtp_config_returns_none_when_partially_set(monkeypatch):
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    _set_env(monkeypatch, {k: v for k, v in SMTP_ENV.items() if k != "SMTP_PASSWORD"})
    assert load_smtp_config() is None


def test_load_smtp_config_parses_recipients_and_strips_whitespace(monkeypatch):
    _set_env(monkeypatch, SMTP_ENV)
    smtp = load_smtp_config()
    assert smtp is not None
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 587
    assert smtp.recipients == ["eferrari@example.com", "jefe@example.com"]


def test_load_smtp_config_defaults_from_addr_to_user(monkeypatch):
    _set_env(monkeypatch, {k: v for k, v in SMTP_ENV.items() if k != "SMTP_FROM"})
    monkeypatch.delenv("SMTP_FROM", raising=False)
    smtp = load_smtp_config()
    assert smtp.from_addr == "bot@example.com"


def test_load_report_schedule_defaults(monkeypatch):
    for key in ["REPORT_DAILY_HOUR", "REPORT_DAILY_MINUTE", "REPORT_WEEKLY_DAY", "REPORT_WEEKLY_HOUR", "REPORT_WEEKLY_MINUTE"]:
        monkeypatch.delenv(key, raising=False)
    schedule = load_report_schedule()
    assert schedule.daily_hour == 8
    assert schedule.daily_minute == 0
    assert schedule.weekly_day == "mon"


def test_load_report_schedule_reads_overrides(monkeypatch):
    monkeypatch.setenv("REPORT_DAILY_HOUR", "9")
    monkeypatch.setenv("REPORT_WEEKLY_DAY", "fri")
    schedule = load_report_schedule()
    assert schedule.daily_hour == 9
    assert schedule.weekly_day == "fri"


def test_load_teams_webhook_url_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    assert load_teams_webhook_url() is None


def test_load_teams_webhook_url_returns_none_when_blank(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "   ")
    assert load_teams_webhook_url() is None


def test_load_teams_webhook_url_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://prod.example.com/workflow/xyz")
    assert load_teams_webhook_url() == "https://prod.example.com/workflow/xyz"


def test_load_teams_report_schedule_defaults(monkeypatch):
    for key in ["TEAMS_REPORT_MORNING_HOUR", "TEAMS_REPORT_MORNING_MINUTE", "TEAMS_REPORT_AFTERNOON_HOUR", "TEAMS_REPORT_AFTERNOON_MINUTE"]:
        monkeypatch.delenv(key, raising=False)
    schedule = load_teams_report_schedule()
    assert schedule.morning_hour == 9
    assert schedule.morning_minute == 0
    assert schedule.afternoon_hour == 16
    assert schedule.afternoon_minute == 30


def test_load_teams_report_schedule_reads_overrides(monkeypatch):
    monkeypatch.setenv("TEAMS_REPORT_MORNING_HOUR", "7")
    monkeypatch.setenv("TEAMS_REPORT_AFTERNOON_MINUTE", "45")
    schedule = load_teams_report_schedule()
    assert schedule.morning_hour == 7
    assert schedule.afternoon_minute == 45
