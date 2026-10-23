"""Cruza el export de Chrome Remote Desktop (Network tab, batchexecute) contra el inventario.

Uso puntual (no forma parte de las fases del proyecto): parsea el archivo
de texto crudo que Chrome guarda desde la respuesta batchexecute de
remotedesktop.google.com/access, y arma un CSV con el ID de host de CRD
por cada fila del inventario, para pegar a mano en una columna nueva del
Excel (INVENTORY_XLSX_PATH sigue siendo de solo lectura para esta
herramienta).

Uso:
    python match_crd.py "C:\\ruta\\al\\archivo.txt" > crd_matches.csv
"""

from __future__ import annotations

import csv
import json
import sys

from app.config import load_inventory_xlsx_path
from app.inventory_loader import load_inventory
from app.matching import normalize


def parse_crd_export(path: str) -> list[tuple[str, str]]:
    """Devuelve [(host_id, nombre_crd), ...] a partir del archivo crudo de Network tab.

    El archivo es la respuesta batchexecute de Google (protegida contra
    XSSI con el prefijo )]}' y envuelta en un formato de chunks). El
    array de dispositivos viene doblemente serializado como JSON dentro
    de un string, así que hay que parsear dos veces.
    """
    with open(path, "r", encoding="cp1252") as f:
        raw = f.read()

    raw = raw.lstrip()
    if raw.startswith(")]}'"):
        raw = raw[4:].lstrip()

    start = raw.index('[["wrb.fr"')
    depth = 0
    end = None
    for i in range(start, len(raw)):
        if raw[i] == "[":
            depth += 1
        elif raw[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("No se encontró un array JSON balanceado en el archivo.")

    outer = json.loads(raw[start:end])
    inner = json.loads(outer[0][2])
    devices_raw = inner[0]

    return [(d[0], d[2]) for d in devices_raw]


def find_crd_match(needle_text: str, devices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Candidatos de CRD cuyo nombre comparte substring normalizado con needle_text."""
    needle = normalize(needle_text)
    if not needle:
        return []
    matches = []
    for host_id, name in devices:
        normalized_name = normalize(name)
        if not normalized_name:
            continue
        if needle in normalized_name or normalized_name in needle:
            matches.append((host_id, name))
    return matches


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("Uso: python match_crd.py <ruta al archivo de export> [ruta CSV de salida]", file=sys.stderr)
        return 1

    devices = parse_crd_export(sys.argv[1])
    inventory = load_inventory(load_inventory_xlsx_path())

    out = open(sys.argv[2], "w", newline="", encoding="utf-8-sig") if len(sys.argv) == 3 else sys.stdout
    writer = csv.writer(out)
    writer.writerow(["sheet_name", "row_number", "nombre_pc", "rak", "anydesk_id", "crd_host_id", "crd_match_note"])

    used_device_ids = set()

    for entry in inventory:
        candidates = find_crd_match(entry.rak, devices) if entry.rak else []
        if not candidates:
            candidates = find_crd_match(entry.nombre_pc, devices)

        if len(candidates) == 1:
            host_id, name = candidates[0]
            used_device_ids.add(host_id)
            note = ""
        elif len(candidates) > 5:
            # RAK/nombre demasiado genérico (ej. "TGACSL" solo) para identificar un equipo.
            host_id, note = "", f"RAK genérico/no identificable ({len(candidates)} coincidencias)"
        elif len(candidates) > 1:
            host_id, name = candidates[0]
            used_device_ids.add(host_id)
            note = f"AMBIGUO: {len(candidates)} candidatos, revisar a mano"
        else:
            host_id, note = "", "sin match en CRD"

        writer.writerow([entry.sheet_name, entry.row_number, entry.nombre_pc, entry.rak, entry.anydesk_id, host_id, note])

    if out is not sys.stdout:
        out.close()

    unmatched_devices = [(hid, name) for hid, name in devices if hid not in used_device_ids]
    print(f"\n# {len(unmatched_devices)} dispositivo(s) de CRD sin match en el inventario:", file=sys.stderr)
    for host_id, name in unmatched_devices:
        print(f"#   {host_id}  {name}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
