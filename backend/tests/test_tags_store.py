import pytest

from app.db import get_connection
from app.tags_store import (
    TAG_CATEGORY_REFURBISH,
    TAG_CATEGORY_RMA,
    TAG_CATEGORY_TESTING,
    DuplicateTagNameError,
    assign_tag,
    create_tag,
    delete_tag,
    get_excluded_host_names,
    get_host_tag_ids,
    list_tags,
    unassign_tag,
    update_tag_category,
)


def test_create_and_list_tag(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Urgente", "#ff0000")
    assert tag.name == "Urgente"
    assert tag.color == "#ff0000"

    tags = list_tags(conn)
    assert len(tags) == 1
    assert tags[0].id == tag.id
    conn.close()


def test_duplicate_tag_name_raises(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    create_tag(conn, "Urgente", "#ff0000")
    with pytest.raises(DuplicateTagNameError):
        create_tag(conn, "Urgente", "#00ff00")
    conn.close()


def test_assign_and_unassign_tag(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Refurbish", "#0000ff")

    assign_tag(conn, tag.id, ["HOST1", "HOST2"])
    mapping = get_host_tag_ids(conn)
    assert mapping["HOST1"] == [tag.id]
    assert mapping["HOST2"] == [tag.id]

    unassign_tag(conn, tag.id, ["HOST1"])
    mapping = get_host_tag_ids(conn)
    assert "HOST1" not in mapping
    assert mapping["HOST2"] == [tag.id]
    conn.close()


def test_assign_is_idempotent(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Urgente", "#ff0000")
    assign_tag(conn, tag.id, ["HOST1"])
    assign_tag(conn, tag.id, ["HOST1"])  # no debe fallar ni duplicar

    mapping = get_host_tag_ids(conn)
    assert mapping["HOST1"] == [tag.id]
    conn.close()


def test_host_can_have_multiple_tags(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag1 = create_tag(conn, "Urgente", "#ff0000")
    tag2 = create_tag(conn, "Refurbish", "#0000ff")

    assign_tag(conn, tag1.id, ["HOST1"])
    assign_tag(conn, tag2.id, ["HOST1"])

    mapping = get_host_tag_ids(conn)
    assert sorted(mapping["HOST1"]) == sorted([tag1.id, tag2.id])
    conn.close()


def test_delete_tag_cascades_to_host_tags(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Urgente", "#ff0000")
    assign_tag(conn, tag.id, ["HOST1"])

    delete_tag(conn, tag.id)

    assert list_tags(conn) == []
    assert get_host_tag_ids(conn) == {}
    conn.close()


def test_get_host_tag_ids_empty_when_no_tags(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    assert get_host_tag_ids(conn) == {}
    conn.close()


def test_create_tag_with_category(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "RMA", "#888888", category=TAG_CATEGORY_RMA)
    assert tag.category == TAG_CATEGORY_RMA

    tags = list_tags(conn)
    assert tags[0].category == TAG_CATEGORY_RMA
    conn.close()


def test_create_tag_defaults_category_to_none(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Urgente", "#ff0000")
    assert tag.category is None
    conn.close()


def test_update_tag_category(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Refurbish", "#0000ff")

    update_tag_category(conn, tag.id, TAG_CATEGORY_TESTING)

    tags = list_tags(conn)
    assert tags[0].category == TAG_CATEGORY_TESTING
    conn.close()


def test_update_tag_category_back_to_none(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "RMA", "#888888", category=TAG_CATEGORY_RMA)

    update_tag_category(conn, tag.id, None)

    tags = list_tags(conn)
    assert tags[0].category is None
    conn.close()


def test_get_excluded_host_names_returns_hosts_with_excluded_tag(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    rma_tag = create_tag(conn, "RMA", "#888888", category=TAG_CATEGORY_RMA)
    normal_tag = create_tag(conn, "Urgente", "#ff0000")

    assign_tag(conn, rma_tag.id, ["HOST_RMA"])
    assign_tag(conn, normal_tag.id, ["HOST_NORMAL"])

    excluded = get_excluded_host_names(conn)

    assert excluded == {"HOST_RMA"}
    conn.close()


def test_get_excluded_host_names_empty_when_no_special_tags(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    tag = create_tag(conn, "Urgente", "#ff0000")
    assign_tag(conn, tag.id, ["HOST1"])

    assert get_excluded_host_names(conn) == set()
    conn.close()


def test_get_excluded_host_names_includes_rma_testing_and_refurbish(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    rma_tag = create_tag(conn, "RMA", "#888888", category=TAG_CATEGORY_RMA)
    testing_tag = create_tag(conn, "Testing", "#00ff00", category=TAG_CATEGORY_TESTING)
    refurbish_tag = create_tag(conn, "Refurbish", "#fff700", category=TAG_CATEGORY_REFURBISH)

    assign_tag(conn, rma_tag.id, ["HOST_RMA"])
    assign_tag(conn, testing_tag.id, ["HOST_TESTING"])
    assign_tag(conn, refurbish_tag.id, ["HOST_REFURBISH"])

    assert get_excluded_host_names(conn) == {"HOST_RMA", "HOST_TESTING", "HOST_REFURBISH"}
    conn.close()
