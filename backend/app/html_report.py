"""Genera un informe HTML autocontenido con el estado general — para descargar y archivar.

A diferencia del resto de la herramienta (que siempre muestra el último
estado en vivo), este archivo es una foto fija del momento en que se
generó: lleva fecha/hora de emisión en el propio documento para que
tenga sentido guardarlo como registro histórico.
"""

from __future__ import annotations

import html
from datetime import datetime

from app.dashboard_service import HostView, get_current_hosts, is_operationally_excluded
from app.pattern_classifier import Pattern
from app.status_parser import HostState

_PATTERN_LABELS = {
    Pattern.CHRONIC: "Crónico",
    Pattern.INTERMITTENT: "Intermitente",
    Pattern.RECENT: "Reciente",
    Pattern.RECOVERED: "Recuperado",
    Pattern.STABLE: "Estable",
}

_STATE_LABELS = {HostState.UP: "Activo", HostState.DOWN: "Caído", HostState.UNREACHABLE: "Inaccesible"}


def _esc(value: object) -> str:
    return html.escape(str(value))


def _ambiente_stats(hosts: list[HostView]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for h in hosts:
        ambiente = h.entry.cliente if h.entry else "Sin identificar"
        row = stats.setdefault(ambiente, {"total": 0, "up": 0, "down": 0, "unreachable": 0})
        row["total"] += 1
        if h.current_state == HostState.UP:
            row["up"] += 1
        elif h.current_state == HostState.DOWN:
            row["down"] += 1
        elif h.current_state == HostState.UNREACHABLE:
            row["unreachable"] += 1
    return stats


def _ambiente_cards_html(stats: dict[str, dict[str, int]]) -> str:
    ordered = sorted(stats.items(), key=lambda kv: (-(kv[1]["down"] + kv[1]["unreachable"]), kv[0]))
    cards = []
    for ambiente, s in ordered:
        cards.append(f"""
        <div class="card">
          <div class="card-title">{_esc(ambiente)}</div>
          <div class="card-stats">
            <span class="stat">{s['total']}<small>Total</small></span>
            <span class="stat up">{s['up']}<small>Activos</small></span>
            <span class="stat down">{s['down']}<small>Caídos</small></span>
            <span class="stat unreachable">{s['unreachable']}<small>Inaccesibles</small></span>
          </div>
        </div>""")
    return "".join(cards)


def _pattern_counts(hosts: list[HostView]) -> dict[Pattern, int]:
    counts: dict[Pattern, int] = {}
    for h in hosts:
        counts[h.pattern] = counts.get(h.pattern, 0) + 1
    return counts


def _down_hosts_table_html(hosts: list[HostView]) -> str:
    down = sorted(
        (h for h in hosts if h.current_state != HostState.UP),
        key=lambda h: -h.downtime_seconds,
    )
    if not down:
        return '<p class="empty">Ningún equipo caído. Todo en orden.</p>'

    rows = []
    for h in down:
        nombre = h.entry.nombre_pc if h.entry else "sin identificar"
        ambiente = h.entry.cliente if h.entry else "-"
        rak = h.entry.rak if h.entry else "-"
        rows.append(f"""
        <tr>
          <td><span class="pill {h.current_state.name.lower()}">{_esc(_STATE_LABELS[h.current_state])}</span></td>
          <td>{_esc(nombre)}</td>
          <td>{_esc(ambiente)}</td>
          <td class="mono">{_esc(h.host_name)}</td>
          <td class="mono">{_esc(rak)}</td>
          <td>{_esc(h.downtime_human)}</td>
          <td>{_esc(_PATTERN_LABELS.get(h.pattern, h.pattern.value))}</td>
          <td>{_esc(h.plugin_output)}</td>
        </tr>""")

    return f"""
    <table>
      <thead><tr><th>Estado</th><th>Equipo</th><th>Ambiente</th><th>Host (Nagios)</th><th>RAK</th><th>Tiempo caído</th><th>Patrón</th><th>Detalle</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def build_html_report() -> str:
    """Arma el informe HTML completo con el estado actual. Excluye equipos RMA/Testing."""
    hosts = [h for h in get_current_hosts() if not is_operationally_excluded(h)]
    total = len(hosts)
    up = sum(1 for h in hosts if h.current_state == HostState.UP)
    down = sum(1 for h in hosts if h.current_state == HostState.DOWN)
    unreachable = sum(1 for h in hosts if h.current_state == HostState.UNREACHABLE)

    emitted_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    ambiente_cards = _ambiente_cards_html(_ambiente_stats(hosts))
    pattern_counts = _pattern_counts(hosts)
    pattern_rows = "".join(
        f'<div class="pattern-row"><span>{_esc(_PATTERN_LABELS[p])}</span><b>{pattern_counts.get(p, 0)}</b></div>'
        for p in [Pattern.CHRONIC, Pattern.INTERMITTENT, Pattern.RECENT, Pattern.RECOVERED, Pattern.STABLE]
        if pattern_counts.get(p, 0) > 0
    )
    down_table = _down_hosts_table_html(hosts)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Informe de Estado — Nagios CSL Dashboard — {emitted_at}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f4f5f7; color: #1a1d21; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 2px; }}
  .subtitle {{ color: #5a6472; font-size: 13px; margin-bottom: 20px; }}
  .panel {{ background: #ffffff; border: 1px solid #d8dbe0; border-radius: 8px; padding: 16px; margin-bottom: 18px; }}
  .panel h2 {{ font-size: 15px; margin: 0 0 12px; }}
  .hero-tiles {{ display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }}
  .hero-tile {{ flex: 1; min-width: 160px; background: #ffffff; border: 1px solid #d8dbe0; border-radius: 8px; padding: 14px 16px; }}
  .hero-tile .value {{ font-size: 28px; font-weight: 700; display: block; }}
  .hero-tile .label {{ font-size: 12px; color: #5a6472; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }}
  .card {{ border: 1px solid #d8dbe0; border-radius: 8px; padding: 12px 14px; background: #f4f5f7; }}
  .card-title {{ font-weight: 700; font-size: 14px; margin-bottom: 10px; }}
  .card-stats {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .stat {{ display: flex; flex-direction: column; align-items: center; min-width: 38px; font-size: 17px; font-weight: 700; }}
  .stat small {{ font-size: 10px; color: #5a6472; font-weight: 400; text-transform: uppercase; }}
  .stat.up {{ color: #1a8f4c; }}
  .stat.down {{ color: #c62828; }}
  .stat.unreachable {{ color: #b8710a; }}
  .pattern-row {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 13px; border-bottom: 1px solid #eef0f2; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 10px; color: #5a6472; border-bottom: 1px solid #d8dbe0; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #eef0f2; }}
  .mono {{ font-family: "Cascadia Code", "Consolas", monospace; color: #5a6472; }}
  .pill {{ padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .pill.down {{ background: #fbe6e6; color: #c62828; }}
  .pill.unreachable {{ background: #fbeecd; color: #b8710a; }}
  .empty {{ color: #5a6472; padding: 12px 0; }}
</style>
</head>
<body>
  <h1>Informe de Estado — Nagios CSL Dashboard</h1>
  <div class="subtitle">Emitido: {emitted_at}</div>

  <div class="hero-tiles">
    <div class="hero-tile"><span class="value">{total}</span><span class="label">Equipos monitoreados</span></div>
    <div class="hero-tile"><span class="value">{down + unreachable}</span><span class="label">Equipos con problemas</span></div>
  </div>

  <div class="panel">
    <h2>Estado de los equipos</h2>
    <div class="card-stats">
      <span class="stat">{total}<small>Total</small></span>
      <span class="stat up">{up}<small>Activos</small></span>
      <span class="stat down">{down}<small>Caídos</small></span>
      <span class="stat unreachable">{unreachable}<small>Inaccesibles</small></span>
    </div>
  </div>

  <div class="panel">
    <h2>Por ambiente</h2>
    <div class="card-grid">{ambiente_cards}</div>
  </div>

  <div class="panel">
    <h2>Distribución de patrones</h2>
    {pattern_rows or '<p class="empty">Sin datos todavía.</p>'}
  </div>

  <div class="panel">
    <h2>Equipos caídos</h2>
    {down_table}
  </div>
</body>
</html>
"""
