"""Envío de reportes a un canal de Microsoft Teams vía un flujo de Power Automate.

Microsoft deprecó los "Incoming Webhook" clásicos de Teams — el
reemplazo recomendado es un flujo de Power Automate con el disparador
"Cuando se recibe una solicitud HTTP", que expone su propia URL y hace
de puente hacia el canal. La herramienta le pega un POST con JSON a
esa URL; nunca habla directo con la API de Teams.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class TeamsSendError(RuntimeError):
    """No se pudo enviar el reporte al webhook de Power Automate."""


def send_teams_card(webhook_url: str, card: dict) -> None:
    """Manda una Adaptive Card al webhook. El flujo la publica con "Post adaptive card"."""
    try:
        response = httpx.post(webhook_url, json={"card": card}, timeout=30)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TeamsSendError(f"No se pudo enviar el reporte a Teams: {exc}") from exc

    logger.info("Reporte enviado a Teams correctamente.")
