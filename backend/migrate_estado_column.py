"""Migración de una sola corrida: agrega la columna `Estado` al inventario.

Hasta ahora, dónde aparecía un equipo lo decidía un tag de categoría especial
(RMA/Testing/Refurbish). Ese modelo no tenía paso de graduación: al salir del
refurbish nadie quitaba el tag, y como el tag escondía al equipo de la pestaña
Equipos, tampoco había dónde ir a quitárselo (el caso Cardenas 2207).

Esta migración traduce los tags vigentes a la nueva columna y deja el resto en
Producción. Después de correrla, el estado se edita desde la herramienta y los
tags vuelven a ser etiquetas libres.

Uso:
    python migrate_estado_column.py            # muestra qué haría, no escribe
    python migrate_estado_column.py --aplicar  # escribe (hace backup antes)
"""

from __future__ import annotations

import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

from app.config import load_database_path, load_inventory_csv_path
from app.db import get_connection
from app.inventory_loader import EstadoOperativo, load_inventory
from app.matching import match_host
from app.tags_store import EXCLUDED_TAG_CATEGORIES, get_host_tag_ids, list_tags

COLUMNA = "Estado"

# Un tag de estas categorías se traduce al estado equivalente.
CATEGORIA_A_ESTADO = {
    "rma": EstadoOperativo.RMA,
    "testing": EstadoOperativo.TESTING,
    "refurbish": EstadoOperativo.REFURBISH,
}

# Equipos que el usuario confirmó que ya salieron del circuito y están en
# servicio, aunque el tag viejo diga otra cosa.
FORZAR_PRODUCCION = {"Cardenas 2207"}


def estados_desde_tags() -> dict[int, EstadoOperativo]:
    """row_number del inventario -> estado que le corresponde por su tag."""
    conn = get_connection(load_database_path())
    try:
        tags_por_id = {t.id: t for t in list_tags(conn)}
        host_tag_ids = get_host_tag_ids(conn)
    finally:
        conn.close()

    inventario = load_inventory(load_inventory_csv_path())
    resultado: dict[int, EstadoOperativo] = {}

    for host_name, tag_ids in host_tag_ids.items():
        categorias = [
            tags_por_id[tid].category
            for tid in tag_ids
            if tid in tags_por_id and tags_por_id[tid].category in EXCLUDED_TAG_CATEGORIES
        ]
        if not categorias:
            continue
        match = match_host(host_name, inventario)
        if match.entry is None:
            print(f"  [aviso] {host_name} tiene tag {categorias[0]} pero no tiene fila en el inventario.")
            continue
        resultado[match.entry.row_number] = CATEGORIA_A_ESTADO[categorias[0]]

    return resultado


def main(aplicar: bool) -> int:
    ruta = Path(load_inventory_csv_path())
    por_fila = estados_desde_tags()

    with open(ruta, "r", encoding="utf-8-sig", newline="") as f:
        filas = list(csv.reader(f))

    if not filas:
        print("El inventario está vacío.")
        return 1

    encabezado = [c.strip() for c in filas[0]]
    if COLUMNA in encabezado:
        print(f"La columna '{COLUMNA}' ya existe. Nada que migrar.")
        return 0

    idx_nombre = encabezado.index("Nombre PC") if "Nombre PC" in encabezado else 0
    nuevas = [encabezado + [COLUMNA]]
    cambios = []

    for row_number, fila in enumerate(filas[1:], start=2):
        if not fila or all(not c.strip() for c in fila):
            nuevas.append(fila)
            continue
        fila = fila + [""] * (len(encabezado) - len(fila))
        nombre = fila[idx_nombre].strip() if idx_nombre < len(fila) else ""

        if nombre in FORZAR_PRODUCCION:
            estado = EstadoOperativo.PRODUCCION
            if row_number in por_fila:
                cambios.append((nombre, f"{por_fila[row_number].value} -> Produccion (forzado)"))
        else:
            estado = por_fila.get(row_number, EstadoOperativo.PRODUCCION)
            if row_number in por_fila:
                cambios.append((nombre, estado.value))

        nuevas.append(fila + [estado.value])

    print(f"Filas de inventario: {len(filas) - 1}")
    print(f"Equipos que NO quedan en Producción: {len([c for c in cambios if 'Produccion' not in c[1]])}")
    for nombre, estado in cambios:
        print(f"  {nombre:<34} {estado}")

    if not aplicar:
        print("\nSimulación. Volvé a correr con --aplicar para escribir.")
        return 0

    respaldo = ruta.with_name(f"{ruta.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{ruta.suffix}")
    shutil.copy2(ruta, respaldo)
    print(f"\nRespaldo: {respaldo.name}")

    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(nuevas)

    print(f"Columna '{COLUMNA}' agregada a {ruta.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(aplicar="--aplicar" in sys.argv))
