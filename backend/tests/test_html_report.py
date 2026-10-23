from app.dashboard_service import HostView
from app.inventory_loader import EstadoOperativo, InventoryEntry
from app.html_report import build_html_report
from app.matching import MatchType
from app.pattern_classifier import Pattern
from app.status_parser import HostState
from app.tags_store import TAG_CATEGORY_RMA, Tag


def make_view(host_name, state, pattern, downtime_seconds=0, downtime_human="-", nombre_pc=None,
              cliente=None, rak="RAK123", tags=None, estado=EstadoOperativo.PRODUCCION):
    entry = None
    if nombre_pc:
        entry = InventoryEntry(nombre_pc=nombre_pc, rak=rak, anydesk_id="1",
                               cliente=cliente or "GCBA", row_number=2, estado=estado)
    return HostView(
        host_name=host_name, current_state=state, downtime_seconds=downtime_seconds,
        downtime_human=downtime_human, plugin_output="plugin output aqui", last_check=1000, polled_at=1000,
        pattern=pattern, match_type=MatchType.RAK if entry else MatchType.NO_MATCH, entry=entry,
        tags=tags or [], services=[],
    )


def test_report_contains_emission_timestamp(monkeypatch):
    import app.html_report as mod
    monkeypatch.setattr(mod, "get_current_hosts", lambda: [make_view("H1", HostState.UP, Pattern.STABLE)])

    report = build_html_report()

    assert "Emitido:" in report
    assert "<html" in report


def test_report_shows_all_clear_when_no_down_hosts(monkeypatch):
    import app.html_report as mod
    monkeypatch.setattr(mod, "get_current_hosts", lambda: [make_view("H1", HostState.UP, Pattern.STABLE)])

    report = build_html_report()

    assert "Ningún equipo caído" in report


def test_report_lists_down_hosts_with_details(monkeypatch):
    import app.html_report as mod
    views = [
        make_view("H1", HostState.DOWN, Pattern.CHRONIC, downtime_human="5d 2h", nombre_pc="Sitio A", cliente="GCBA", rak="RAKABC123"),
    ]
    monkeypatch.setattr(mod, "get_current_hosts", lambda: views)

    report = build_html_report()

    assert "Sitio A" in report
    assert "GCBA" in report
    assert "RAKABC123" in report
    assert "5d 2h" in report
    assert "plugin output aqui" in report


def test_report_shows_ambiente_cards(monkeypatch):
    import app.html_report as mod
    views = [
        make_view("H1", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio A", cliente="GCBA"),
        make_view("H2", HostState.UP, Pattern.STABLE, nombre_pc="Sitio B", cliente="Rosario"),
    ]
    monkeypatch.setattr(mod, "get_current_hosts", lambda: views)

    report = build_html_report()

    assert "GCBA" in report
    assert "Rosario" in report


def test_report_excludes_hosts_fuera_de_produccion(monkeypatch):
    import app.html_report as mod
    rma_tag = Tag(id=1, name="RMA", color="#888888", category=TAG_CATEGORY_RMA)
    views = [
        make_view("H1", HostState.DOWN, Pattern.CHRONIC, nombre_pc="En Stock", cliente="STOCK", estado=EstadoOperativo.RMA),
        make_view("H2", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio Real", cliente="GCBA"),
    ]
    monkeypatch.setattr(mod, "get_current_hosts", lambda: views)

    report = build_html_report()

    assert "En Stock" not in report
    assert "Sitio Real" in report


def test_report_escapes_html_in_host_names(monkeypatch):
    import app.html_report as mod
    views = [make_view("H1", HostState.DOWN, Pattern.RECENT, nombre_pc="<script>alert(1)</script>", cliente="GCBA")]
    monkeypatch.setattr(mod, "get_current_hosts", lambda: views)

    report = build_html_report()

    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;" in report
