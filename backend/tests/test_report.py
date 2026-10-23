from app.dashboard_service import HostView
from app.inventory_loader import InventoryEntry
from app.matching import MatchType
from app.pattern_classifier import Pattern
from app.report import build_report_text
from app.status_parser import HostState


def make_view(host_name, state, pattern, downtime_seconds=0, downtime_human="-", nombre_pc=None, cliente=None):
    entry = None
    if nombre_pc:
        entry = InventoryEntry(
            nombre_pc=nombre_pc, rak="RAK", anydesk_id="1", cliente=cliente or "GCBA",
            row_number=2,
        )
    return HostView(
        host_name=host_name, current_state=state, downtime_seconds=downtime_seconds,
        downtime_human=downtime_human, plugin_output="", last_check=1000, polled_at=1000,
        pattern=pattern, match_type=MatchType.RAK if entry else MatchType.NO_MATCH, entry=entry, tags=[], services=[],
    )


def test_daily_report_counts_by_state(monkeypatch):
    import app.report as report_module
    views = [
        make_view("H1", HostState.UP, Pattern.STABLE),
        make_view("H2", HostState.DOWN, Pattern.RECENT, 3600, "1h 0m", "Sitio A", "GCBA"),
        make_view("H3", HostState.UNREACHABLE, Pattern.STABLE),
    ]
    monkeypatch.setattr(report_module, "get_current_hosts", lambda: views)

    subject, body = build_report_text("daily")
    assert "Reporte diario" in body
    assert "Total equipos: 3" in body
    assert "UP: 1" in body
    assert "DOWN: 1" in body
    assert "UNREACHABLE: 1" in body
    assert "Sitio A" in body
    assert "2 caído(s) de 3" in subject


def test_daily_report_no_down_hosts(monkeypatch):
    import app.report as report_module
    views = [make_view("H1", HostState.UP, Pattern.STABLE)]
    monkeypatch.setattr(report_module, "get_current_hosts", lambda: views)

    _, body = build_report_text("daily")
    assert "Ningún equipo caído." in body


def test_down_by_ambiente_groups_correctly(monkeypatch):
    import app.report as report_module
    views = [
        make_view("H1", HostState.DOWN, Pattern.RECENT, nombre_pc="A", cliente="GCBA"),
        make_view("H2", HostState.DOWN, Pattern.RECENT, nombre_pc="B", cliente="GCBA"),
        make_view("H3", HostState.DOWN, Pattern.RECENT, nombre_pc="C", cliente="Rosario"),
    ]
    monkeypatch.setattr(report_module, "get_current_hosts", lambda: views)

    _, body = build_report_text("daily")
    assert "GCBA: 2" in body
    assert "Rosario: 1" in body


def test_unmatched_down_host_labeled_sin_identificar(monkeypatch):
    import app.report as report_module
    views = [make_view("H1", HostState.DOWN, Pattern.RECENT)]
    monkeypatch.setattr(report_module, "get_current_hosts", lambda: views)

    _, body = build_report_text("daily")
    assert "sin identificar" in body
    assert "Sin identificar: 1" in body


def test_weekly_report_includes_pattern_summary(monkeypatch):
    import app.report as report_module
    views = [
        make_view("H1", HostState.DOWN, Pattern.CHRONIC, 700000, "8d 2h 0m", "Crónico A", "GCBA"),
        make_view("H2", HostState.UP, Pattern.INTERMITTENT, nombre_pc="Intermitente B", cliente="GCBA"),
        make_view("H3", HostState.UP, Pattern.STABLE),
    ]
    monkeypatch.setattr(report_module, "get_current_hosts", lambda: views)

    _, body = build_report_text("weekly")
    assert "Resumen semanal de patrones" in body
    assert "Crónicos (>7 días caídos" in body
    assert "Crónico A" in body
    assert "Intermitentes (flapping recurrente): 1" in body
    assert "Intermitente B" in body


def test_daily_report_has_no_weekly_section(monkeypatch):
    import app.report as report_module
    views = [make_view("H1", HostState.UP, Pattern.STABLE)]
    monkeypatch.setattr(report_module, "get_current_hosts", lambda: views)

    _, body = build_report_text("daily")
    assert "Resumen semanal" not in body
