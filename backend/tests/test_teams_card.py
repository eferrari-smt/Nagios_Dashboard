from app.dashboard_service import HostView
from app.inventory_loader import EstadoOperativo, InventoryEntry
from app.matching import MatchType
from app.pattern_classifier import Pattern
from app.status_parser import HostState
from app.tags_store import TAG_CATEGORY_RMA, TAG_CATEGORY_TESTING, Tag
from app.teams_card import build_daily_report_card


def make_view(host_name, state, pattern, downtime_seconds=0, downtime_human="-", nombre_pc=None, cliente=None, tags=None,
              estado=EstadoOperativo.PRODUCCION):
    entry = None
    if nombre_pc:
        entry = InventoryEntry(nombre_pc=nombre_pc, rak="RAK", anydesk_id="1",
                               cliente=cliente or "GCBA", row_number=2, estado=estado)
    return HostView(
        host_name=host_name, current_state=state, downtime_seconds=downtime_seconds,
        downtime_human=downtime_human, plugin_output="", last_check=1000, polled_at=1000,
        pattern=pattern, match_type=MatchType.RAK if entry else MatchType.NO_MATCH, entry=entry,
        tags=tags or [], services=[],
    )


def test_card_is_well_formed_adaptive_card(monkeypatch):
    import app.teams_card as teams_card_module
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: [make_view("H1", HostState.UP, Pattern.STABLE)])

    card = build_daily_report_card()

    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert isinstance(card["body"], list)


def test_card_shows_all_clear_message_when_nothing_down(monkeypatch):
    import app.teams_card as teams_card_module
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: [make_view("H1", HostState.UP, Pattern.STABLE)])

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    assert any("Ningún equipo caído" in t for t in texts)


def test_card_lists_down_ambiente_breakdown(monkeypatch):
    import app.teams_card as teams_card_module
    views = [
        make_view("H1", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio A", cliente="GCBA"),
        make_view("H2", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio B", cliente="GCBA"),
        make_view("H3", HostState.UNREACHABLE, Pattern.RECENT, nombre_pc="Sitio C", cliente="Rosario"),
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    containers = [b for b in card["body"] if b.get("type") == "Container"]
    assert len(containers) == 1
    facts = containers[0]["items"][1]["facts"]
    assert {"title": "GCBA", "value": "2"} in facts
    assert {"title": "Rosario", "value": "1"} in facts


def test_card_lists_every_down_host_with_no_cap(monkeypatch):
    import app.teams_card as teams_card_module
    views = [
        make_view(f"H{i}", HostState.DOWN, Pattern.CHRONIC, downtime_seconds=1000 - i,
                  downtime_human=f"{i}d", nombre_pc=f"Sitio {i}", cliente="Rosario")
        for i in range(8)
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    urgent_lines = [t for t in texts if "Sitio" in t]
    assert len(urgent_lines) == 8
    assert not any("más" in t for t in texts)  # ya no hay nota de "y N más"


def test_card_prioritizes_gcba_ahead_of_downtime(monkeypatch):
    import app.teams_card as teams_card_module
    views = [
        make_view("H1", HostState.DOWN, Pattern.CHRONIC, downtime_seconds=99999, nombre_pc="Sitio Rosario", cliente="Rosario"),
        make_view("H2", HostState.DOWN, Pattern.RECENT, downtime_seconds=10, nombre_pc="Sitio GCBA", cliente="GCBA"),
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"] if "Sitio" in b.get("text", "")]
    assert "Sitio GCBA" in texts[0]
    assert "Sitio Rosario" in texts[1]


def test_card_shows_rak_for_matched_host(monkeypatch):
    import app.teams_card as teams_card_module
    entry = InventoryEntry(nombre_pc="Sitio A", rak="RAKPARTICULAR123", anydesk_id="1", cliente="GCBA", row_number=2)
    view = HostView(
        host_name="H1", current_state=HostState.DOWN, downtime_seconds=10, downtime_human="10s",
        plugin_output="", last_check=1000, polled_at=1000, pattern=Pattern.RECENT,
        match_type=MatchType.RAK, entry=entry, tags=[], services=[],
    )
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: [view])

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    assert any("RAKPARTICULAR123" in t for t in texts)


def test_card_falls_back_to_host_name_when_unmatched(monkeypatch):
    import app.teams_card as teams_card_module
    view = make_view("HOST_SIN_MATCH", HostState.DOWN, Pattern.RECENT)
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: [view])

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    assert any("RAK: HOST_SIN_MATCH" in t for t in texts)


def test_card_excludes_hosts_en_rma(monkeypatch):
    import app.teams_card as teams_card_module
    rma_tag = Tag(id=1, name="RMA", color="#888888", category=TAG_CATEGORY_RMA)
    views = [
        make_view("H1", HostState.DOWN, Pattern.CHRONIC, nombre_pc="En Stock", cliente="STOCK", estado=EstadoOperativo.RMA),
        make_view("H2", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio Real", cliente="GCBA"),
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    assert not any("En Stock" in t for t in texts)
    assert any("Sitio Real" in t for t in texts)


def test_card_excludes_hosts_en_testing(monkeypatch):
    import app.teams_card as teams_card_module
    testing_tag = Tag(id=2, name="Testing", color="#00ff00", category=TAG_CATEGORY_TESTING)
    views = [
        make_view("H1", HostState.DOWN, Pattern.CHRONIC, nombre_pc="PC de pruebas", cliente="Oficina", estado=EstadoOperativo.TESTING),
        make_view("H2", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio Real", cliente="GCBA"),
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    assert not any("PC de pruebas" in t for t in texts)
    assert any("Sitio Real" in t for t in texts)


def test_card_excludes_internal_test_vm(monkeypatch):
    import app.teams_card as teams_card_module
    views = [
        make_view("smartmation-linux-01-NUC8CCHB", HostState.DOWN, Pattern.CHRONIC, nombre_pc="Linux 01 (Test)", cliente="Oficina"),
        make_view("H2", HostState.DOWN, Pattern.RECENT, nombre_pc="Sitio Real", cliente="GCBA"),
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    texts = [b.get("text", "") for b in card["body"]]
    assert not any("Linux 01" in t for t in texts)
    assert any("Sitio Real" in t for t in texts)


def test_card_excludes_test_vm_from_counts_too(monkeypatch):
    import app.teams_card as teams_card_module
    views = [
        make_view("smartmation-linux-01-NUC8CCHB", HostState.DOWN, Pattern.CHRONIC, nombre_pc="Linux 01 (Test)", cliente="Oficina"),
        make_view("H2", HostState.UP, Pattern.STABLE, nombre_pc="Sitio Real", cliente="GCBA"),
    ]
    monkeypatch.setattr(teams_card_module, "get_current_hosts", lambda: views)

    card = build_daily_report_card()

    stat_values = [
        col["items"][0]["text"]
        for b in card["body"] if b.get("type") == "ColumnSet"
        for col in b["columns"]
    ]
    assert stat_values == ["1", "1", "0", "0"]  # total/activos/caidos/inaccesibles, sin contar la VM de test
