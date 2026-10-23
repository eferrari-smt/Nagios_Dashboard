from app.inventory_loader import InventoryEntry
from app.matching import MatchType, find_inventory_duplicates, match_host, normalize


def entry(nombre_pc="", rak="", anydesk_id="", cliente="GCBA", row=2):
    return InventoryEntry(
        nombre_pc=nombre_pc, rak=rak, anydesk_id=anydesk_id,
        cliente=cliente, row_number=row,
    )


def test_normalize_strips_non_alnum_and_uppercases():
    assert normalize("tgacsl-612qwz0y2l44pkhx0mx9") == "TGACSL612QWZ0Y2L44PKHX0MX9"
    assert normalize("-84X44840U58UTI2407YC") == "84X44840U58UTI2407YC"
    assert normalize("") == ""


def test_matches_truncated_nagios_name_as_substring_of_rak():
    inv = [entry(rak="MPC1BBAWT11MY260617", nombre_pc="Some place")]
    result = match_host("MPC1BBAWT11MY26", inv)
    assert result.match_type == MatchType.RAK
    assert result.entry.rak == "MPC1BBAWT11MY260617"


def test_falls_back_to_nombre_pc_when_rak_empty():
    inv = [entry(rak="", nombre_pc="21Y9OJ4H8VX6CRA", cliente="STOCK")]
    result = match_host("21Y9OJ4H8VX6CRA", inv)
    assert result.match_type == MatchType.NOMBRE_PC
    assert result.entry.cliente == "STOCK"


def test_no_match_returns_no_match_type_and_none_entry():
    inv = [entry(rak="COMPLETELYDIFFERENT")]
    result = match_host("UNRELATEDHOST12", inv)
    assert result.match_type == MatchType.NO_MATCH
    assert result.entry is None


def test_duplicate_candidates_prioritizes_non_stock():
    inv = [
        entry(rak="SHAREDRAK000000000A", cliente="STOCK", row=5),
        entry(rak="SHAREDRAK000000000A", cliente="GCBA", row=9),
    ]
    result = match_host("SHAREDRAK000000000A"[:15], inv)
    assert result.match_type == MatchType.RAK
    assert result.entry.cliente == "GCBA"
    assert result.candidate_count == 2


def test_duplicate_candidates_all_stock_uses_first():
    inv = [
        entry(rak="SHAREDRAK000000000A", cliente="STOCK", row=5),
        entry(rak="SHAREDRAK000000000A", cliente="STOCK", row=9),
    ]
    result = match_host("SHAREDRAK000000000A"[:15], inv)
    assert result.entry.row_number == 5


def test_empty_host_name_is_no_match():
    result = match_host("", [entry(rak="ANYTHING")])
    assert result.match_type == MatchType.NO_MATCH


def test_find_inventory_duplicates_groups_by_rak_and_anydesk_id():
    inv = [
        entry(rak="DUPRAK00000000000001", anydesk_id="111", cliente="GCBA", row=2),
        entry(rak="dup-rak-00000000000001", anydesk_id="111", cliente="RESTO", row=8),
        entry(rak="UNIQUERAK000000000002", anydesk_id="222", cliente="GCBA", row=3),
    ]
    duplicates = find_inventory_duplicates(inv)
    assert len(duplicates) == 1
    assert len(duplicates[0].entries) == 2
    assert duplicates[0].anydesk_id == "111"


def test_find_inventory_duplicates_ignores_entries_missing_rak_or_id():
    inv = [
        entry(rak="", anydesk_id="111"),
        entry(rak="SOMERAK0000000000003", anydesk_id=""),
    ]
    assert find_inventory_duplicates(inv) == []
