"""Generación del contenido de los reportes por mail (diario/semanal)."""

from __future__ import annotations

from app.dashboard_service import HostView, get_current_hosts
from app.pattern_classifier import Pattern
from app.status_parser import HostState


def _counts_by_state(hosts: list[HostView]) -> dict[str, int]:
    return {
        "UP": sum(1 for h in hosts if h.current_state == HostState.UP),
        "DOWN": sum(1 for h in hosts if h.current_state == HostState.DOWN),
        "UNREACHABLE": sum(1 for h in hosts if h.current_state == HostState.UNREACHABLE),
    }


def _down_by_ambiente(hosts: list[HostView]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for h in hosts:
        if h.current_state == HostState.UP:
            continue
        ambiente = h.entry.cliente if h.entry else "Sin identificar"
        counts[ambiente] = counts.get(ambiente, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def build_report_text(kind: str) -> tuple[str, str]:
    """Arma (asunto, cuerpo en texto plano) para kind='daily' o 'weekly'."""
    hosts = get_current_hosts()
    counts = _counts_by_state(hosts)
    down_hosts = sorted(
        (h for h in hosts if h.current_state != HostState.UP),
        key=lambda h: -h.downtime_seconds,
    )
    by_ambiente = _down_by_ambiente(hosts)
    label = "diario" if kind == "daily" else "semanal"

    lines = [f"Reporte {label} — Nagios CSL Dashboard", ""]
    lines.append(f"Total equipos: {len(hosts)}")
    lines.append(f"  UP: {counts['UP']}")
    lines.append(f"  DOWN: {counts['DOWN']}")
    lines.append(f"  UNREACHABLE: {counts['UNREACHABLE']}")
    lines.append("")

    if by_ambiente:
        lines.append("DOWN por ambiente:")
        for ambiente, n in by_ambiente.items():
            lines.append(f"  {ambiente}: {n}")
        lines.append("")

    if down_hosts:
        lines.append("Equipos caídos (ordenados de mayor a menor downtime):")
        for h in down_hosts:
            nombre = h.entry.nombre_pc if h.entry else "sin identificar"
            ambiente = h.entry.cliente if h.entry else "-"
            lines.append(f"  [{h.pattern.value.upper()}] {nombre} ({ambiente}) — {h.downtime_human} — host: {h.host_name}")
    else:
        lines.append("Ningún equipo caído.")

    if kind == "weekly":
        chronic = [h for h in hosts if h.pattern == Pattern.CHRONIC]
        intermittent = [h for h in hosts if h.pattern == Pattern.INTERMITTENT]
        lines.append("")
        lines.append("--- Resumen semanal de patrones ---")
        lines.append(f"Crónicos (>7 días caídos, requieren intervención en terreno): {len(chronic)}")
        for h in chronic:
            nombre = h.entry.nombre_pc if h.entry else "sin identificar"
            lines.append(f"  - {nombre} — {h.downtime_human} — host: {h.host_name}")
        lines.append(f"Intermitentes (flapping recurrente): {len(intermittent)}")
        for h in intermittent:
            nombre = h.entry.nombre_pc if h.entry else "sin identificar"
            lines.append(f"  - {nombre} — host: {h.host_name}")

    down_count = counts["DOWN"] + counts["UNREACHABLE"]
    subject = f"Nagios CSL Dashboard — Reporte {label} — {down_count} caído(s) de {len(hosts)}"
    return subject, "\n".join(lines)
