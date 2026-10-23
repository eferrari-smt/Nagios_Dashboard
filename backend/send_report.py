"""Genera (y opcionalmente envía) un reporte manualmente, sin esperar al horario programado.

Uso:
    python send_report.py daily              # solo imprime, no requiere SMTP ni Teams
    python send_report.py weekly --send      # imprime y además lo manda por mail
    python send_report.py daily --teams      # imprime y además lo manda a Teams
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import load_smtp_config, load_teams_webhook_url
from app.mailer import ReportSendError, send_report_email
from app.report import build_report_text
from app.teams_card import build_daily_report_card
from app.teams_notifier import TeamsSendError, send_teams_card

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("send_report")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["daily", "weekly"])
    parser.add_argument("--send", action="store_true", help="Enviar de verdad por mail (si no, solo imprime)")
    parser.add_argument("--teams", action="store_true", help="Enviar de verdad a Teams (vía el webhook de Power Automate)")
    args = parser.parse_args()

    subject, body = build_report_text(args.kind)
    print(f"Asunto: {subject}\n")
    print(body)

    if args.send:
        smtp = load_smtp_config()
        if smtp is None:
            logger.error("Falta configuración SMTP en .env — no se puede enviar todavía.")
            return 1
        try:
            send_report_email(smtp, subject, body)
        except ReportSendError as exc:
            logger.error(str(exc))
            return 1
        logger.info("Enviado por mail a %s", ", ".join(smtp.recipients))

    if args.teams:
        webhook_url = load_teams_webhook_url()
        if webhook_url is None:
            logger.error("Falta TEAMS_WEBHOOK_URL en .env — no se puede enviar todavía.")
            return 1
        card = build_daily_report_card()
        try:
            send_teams_card(webhook_url, card)
        except TeamsSendError as exc:
            logger.error(str(exc))
            return 1
        logger.info("Enviado a Teams.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
