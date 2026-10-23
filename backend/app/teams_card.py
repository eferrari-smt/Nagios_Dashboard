"""Arma la Adaptive Card del reporte diario para Teams.

Se manda como JSON dentro del payload al webhook de Power Automate
(bajo la clave "card") — el flujo la pasa tal cual a la acción "Post
card in a chat or channel", sin tocar el contenido. Todo el diseño
vive acá (no en las expresiones del flujo) para poder iterarlo y
testearlo como cualquier otro módulo.

Pensada para una lectura rápida ("vista de insight"): números grandes
arriba, color por gravedad, y la lista completa de caídos priorizando
GCBA primero (a pedido del usuario).
"""

from __future__ import annotations

from app.dashboard_service import HostView, get_current_hosts, is_operationally_excluded
from app.pattern_classifier import Pattern
from app.report import _counts_by_state, _down_by_ambiente
from app.status_parser import HostState

# smartmation-linux-01-NUC8CCHB es una VM de test interna, no un CSL real —
# se excluye del reporte a pedido del usuario (siempre está caída y no aporta nada).
_EXCLUDED_HOST_NAMES = {"smartmation-linux-01-NUC8CCHB"}

_PATTERN_EMOJI = {
    Pattern.CHRONIC: "🔴",
    Pattern.INTERMITTENT: "🟠",
    Pattern.RECENT: "🔵",
    Pattern.RECOVERED: "🟢",
    Pattern.STABLE: "⚪",
}

_PRIORITY_AMBIENTE = "GCBA"


def _fact(title: str, value: object) -> dict:
    return {"title": title, "value": str(value)}


def _stat_column(value: object, label: str, color: str | None = None) -> dict:
    value_block = {
        "type": "TextBlock", "text": str(value), "size": "ExtraLarge",
        "weight": "Bolder", "horizontalAlignment": "Center",
    }
    if color:
        value_block["color"] = color
    return {
        "type": "Column",
        "width": "stretch",
        "items": [
            value_block,
            {"type": "TextBlock", "text": label, "horizontalAlignment": "Center", "isSubtle": True, "wrap": True, "size": "Small"},
        ],
    }


def _sort_key(h: HostView) -> tuple[int, int]:
    ambiente = h.entry.cliente if h.entry else ""
    is_priority = ambiente != _PRIORITY_AMBIENTE  # False (0) ordena antes que True (1)
    return (is_priority, -h.downtime_seconds)


def build_daily_report_card() -> dict:
    """Arma la Adaptive Card completa del reporte diario con el estado actual."""
    hosts = [
        h for h in get_current_hosts()
        if h.host_name not in _EXCLUDED_HOST_NAMES and not is_operationally_excluded(h)
    ]
    counts = _counts_by_state(hosts)
    total = len(hosts)
    down_count = counts["DOWN"] + counts["UNREACHABLE"]

    down_hosts = sorted((h for h in hosts if h.current_state != HostState.UP), key=_sort_key)
    by_ambiente = _down_by_ambiente(hosts)

    body: list[dict] = [
        {
            "type": "TextBlock",
            "text": "📊 Reporte diario — Nagios CSL Dashboard",
            "size": "Large",
            "weight": "Bolder",
            "wrap": True,
        },
        {
            "type": "ColumnSet",
            "columns": [
                _stat_column(total, "Total equipos"),
                _stat_column(counts["UP"], "Activos", "Good"),
                _stat_column(counts["DOWN"], "Caídos", "Attention"),
                _stat_column(counts["UNREACHABLE"], "Inaccesibles", "Warning"),
            ],
        },
    ]

    if down_count == 0:
        body.append({
            "type": "TextBlock",
            "text": "✅ Ningún equipo caído. Todo en orden.",
            "wrap": True,
            "color": "Good",
            "weight": "Bolder",
            "spacing": "Medium",
        })
        return {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
        }

    if by_ambiente:
        body.append({
            "type": "Container",
            "style": "attention",
            "spacing": "Medium",
            "items": [
                {"type": "TextBlock", "text": "Caídos por ambiente", "weight": "Bolder"},
                {"type": "FactSet", "facts": [_fact(a, n) for a, n in by_ambiente.items()]},
            ],
        })

    body.append({"type": "TextBlock", "text": "Equipos caídos", "weight": "Bolder", "spacing": "Medium"})

    for h in down_hosts:
        nombre = h.entry.nombre_pc if h.entry else "sin identificar"
        ambiente = h.entry.cliente if h.entry else "-"
        rak = h.entry.rak if h.entry and h.entry.rak else h.host_name
        emoji = _PATTERN_EMOJI.get(h.pattern, "⚪")
        body.append({
            "type": "TextBlock",
            "text": f"{emoji} **{nombre}** ({ambiente}) — RAK: {rak} — caído hace {h.downtime_human}",
            "wrap": True,
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
