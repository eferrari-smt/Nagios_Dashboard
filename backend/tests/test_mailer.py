import base64
import smtplib

import pytest

from app.config import SmtpConfig
from app.mailer import ReportSendError, send_report_email

SMTP = SmtpConfig(
    host="smtp.gmail.com", port=587, user="bot@example.com", password="secret",
    from_addr="bot@example.com", recipients=["eferrari@example.com", "jefe@example.com"],
)


class FakeSmtpServer:
    def __init__(self, *args, **kwargs):
        self.login_calls = []
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.login_calls.append((user, password))

    def sendmail(self, from_addr, recipients, message):
        self.sent.append((from_addr, recipients, message))


def test_send_report_email_success(monkeypatch):
    instances = []

    def fake_smtp(*args, **kwargs):
        server = FakeSmtpServer(*args, **kwargs)
        instances.append(server)
        return server

    monkeypatch.setattr(smtplib, "SMTP", fake_smtp)

    send_report_email(SMTP, "Asunto de prueba", "Cuerpo de prueba")

    assert len(instances) == 1
    server = instances[0]
    assert server.login_calls == [("bot@example.com", "secret")]
    assert len(server.sent) == 1
    from_addr, recipients, message = server.sent[0]
    assert from_addr == "bot@example.com"
    assert recipients == ["eferrari@example.com", "jefe@example.com"]
    assert "Asunto de prueba" in message
    encoded_body = message.split("\n\n", 1)[1].strip()
    assert base64.b64decode(encoded_body).decode("utf-8") == "Cuerpo de prueba"


def test_send_report_email_wraps_smtp_exception(monkeypatch):
    def fake_smtp(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", fake_smtp)

    with pytest.raises(ReportSendError):
        send_report_email(SMTP, "Asunto", "Cuerpo")


def test_send_report_email_wraps_connection_error(monkeypatch):
    def fake_smtp(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(smtplib, "SMTP", fake_smtp)

    with pytest.raises(ReportSendError):
        send_report_email(SMTP, "Asunto", "Cuerpo")
