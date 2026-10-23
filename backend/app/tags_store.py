"""Persistencia de tags personalizables (organización manual, no viene de Nagios ni del inventario)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Categorías especiales: un tag con una de estas categorías saca al host de las
# vistas de "problemas" (Estado por defecto, Alarmas, Resumen) y lo manda a la
# pestaña dedicada "Pendientes" — pensado para equipos que están caídos/inactivos
# a propósito (en stock, en reparación, en pruebas, o pendientes de alta en
# producción), que de otro modo ensucian las alarmas mostrando como "caído"
# algo que está caído a propósito.
TAG_CATEGORY_RMA = "rma"
TAG_CATEGORY_TESTING = "testing"
TAG_CATEGORY_REFURBISH = "refurbish"
EXCLUDED_TAG_CATEGORIES = {TAG_CATEGORY_RMA, TAG_CATEGORY_TESTING, TAG_CATEGORY_REFURBISH}


class DuplicateTagNameError(RuntimeError):
    """Ya existe un tag con ese nombre."""


@dataclass(frozen=True)
class Tag:
    id: int
    name: str
    color: str
    category: str | None = None


def create_tag(conn: sqlite3.Connection, name: str, color: str, category: str | None = None) -> Tag:
    try:
        cursor = conn.execute(
            "INSERT INTO tags (name, color, category) VALUES (?, ?, ?)", (name, color, category)
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateTagNameError(f"Ya existe un tag llamado '{name}'.") from exc
    conn.commit()
    return Tag(id=cursor.lastrowid, name=name, color=color, category=category)


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    """Borra el tag y sus asignaciones (host_tags tiene ON DELETE CASCADE)."""
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()


def update_tag_category(conn: sqlite3.Connection, tag_id: int, category: str | None) -> None:
    conn.execute("UPDATE tags SET category = ? WHERE id = ?", (category, tag_id))
    conn.commit()


def list_tags(conn: sqlite3.Connection) -> list[Tag]:
    rows = conn.execute("SELECT id, name, color, category FROM tags ORDER BY name COLLATE NOCASE").fetchall()
    return [Tag(id=r[0], name=r[1], color=r[2], category=r[3]) for r in rows]


def assign_tag(conn: sqlite3.Connection, tag_id: int, host_names: list[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO host_tags (host_name, tag_id) VALUES (?, ?)",
        [(host_name, tag_id) for host_name in host_names],
    )
    conn.commit()


def unassign_tag(conn: sqlite3.Connection, tag_id: int, host_names: list[str]) -> None:
    conn.executemany(
        "DELETE FROM host_tags WHERE host_name = ? AND tag_id = ?",
        [(host_name, tag_id) for host_name in host_names],
    )
    conn.commit()


def get_host_tag_ids(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """host_name -> [tag_id, ...] para adjuntar a cada host de una sola consulta."""
    rows = conn.execute("SELECT host_name, tag_id FROM host_tags").fetchall()
    result: dict[str, list[int]] = {}
    for host_name, tag_id in rows:
        result.setdefault(host_name, []).append(tag_id)
    return result


def get_excluded_host_names(conn: sqlite3.Connection) -> set[str]:
    """Host names con algún tag de categoría RMA/Testing — sin tocar el inventario.

    Se usa donde no se puede depender del CSV de inventario (ej. Insights,
    que debe seguir funcionando aunque el inventario esté temporalmente
    inaccesible): esto solo necesita la SQLite de tags.
    """
    tags_by_id = {t.id: t for t in list_tags(conn)}
    excluded_tag_ids = {tid for tid, t in tags_by_id.items() if t.category in EXCLUDED_TAG_CATEGORIES}
    if not excluded_tag_ids:
        return set()

    host_tag_ids = get_host_tag_ids(conn)
    return {
        host_name for host_name, tag_ids in host_tag_ids.items()
        if any(tid in excluded_tag_ids for tid in tag_ids)
    }
