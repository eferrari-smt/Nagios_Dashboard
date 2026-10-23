"""Envío de reportes por mail vía SMTP (Gmail u otro proveedor compatible)."""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from app.config import SmtpConfig

logger = logging.getLogger(__name__)


class ReportSendError(RuntimeError):
    """No se pudo enviar el reporte por mail (conexión, auth, etc.)."""


def send_report_email(smtp: SmtpConfig, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp.from_addr
    msg["To"] = ", ".join(smtp.recipients)

    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            server.starttls()
            server.login(smtp.user, smtp.password)
            server.sendmail(smtp.from_addr, smtp.recipients, msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        raise ReportSendError(f"No se pudo enviar el mail: {exc}") from exc

    logger.info("Reporte enviado a %s", ", ".join(smtp.recipients))
