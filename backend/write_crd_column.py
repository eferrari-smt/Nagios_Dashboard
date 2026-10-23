"""Escribe la columna 'ID Chrome Remote Desktop' directamente en el Excel de inventario.

Uso puntual (una sola corrida, no forma parte del flujo normal de la
herramienta — la app en sí nunca escribe en el Excel). Reutiliza el
parseo/matching de match_crd.py, pero en vez de generar un CSV aparte,
escribe los matches de alta confianza directo en el archivo real.

Solo completa filas con un único candidato claro. Los casos ambiguos,
de RAK genérico, o sin match quedan en blanco para revisión manual (ver
crd_host_ids_matched.csv, que sigue siendo la referencia completa).

Uso:
    python write_crd_column.py "<ruta al export de CRD>"
"""

from __future__ import annotations

import sys

import openpyxl

from app.config import load_inventory_xlsx_path
from match_crd import find_crd_match, parse_crd_export

COLUMN_HEADER = "ID Chrome Remote Desktop"


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python write_crd_column.py <ruta al export de CRD>", file=sys.stderr)
        return 1

    devices = parse_crd_export(sys.argv[1])
    xlsx_path = load_inventory_xlsx_path()

    workbook = openpyxl.load_workbook(xlsx_path)

    written = 0
    skipped_ambiguous_or_generic = 0
    skipped_no_match = 0

    for sheet in workbook.worksheets:
        header = [cell.value for cell in sheet[1]]
        try:
            rak_col = header.index("RAK") + 1
        except ValueError:
            continue  # hoja sin columna RAK reconocible, no tocar
        try:
            nombre_col = header.index("Nombre PC") + 1
        except ValueError:
            nombre_col = None

        if COLUMN_HEADER in header:
            crd_col = header.index(COLUMN_HEADER) + 1
        else:
            crd_col = len(header) + 1
            sheet.cell(row=1, column=crd_col, value=COLUMN_HEADER)

        for row_number in range(2, sheet.max_row + 1):
            rak = sheet.cell(row=row_number, column=rak_col).value
            nombre = sheet.cell(row=row_number, column=nombre_col).value if nombre_col else None
            if rak is None and nombre is None:
                continue

            candidates = find_crd_match(str(rak), devices) if rak else []
            if not candidates and nombre:
                candidates = find_crd_match(str(nombre), devices)

            if len(candidates) == 1:
                sheet.cell(row=row_number, column=crd_col, value=candidates[0][0])
                written += 1
            elif len(candidates) > 5 or len(candidates) == 0:
                skipped_no_match += 1
            else:
                skipped_ambiguous_or_generic += 1

    workbook.save(xlsx_path)

    print(f"Columna '{COLUMN_HEADER}' actualizada en {xlsx_path}")
    print(f"  Filas completadas (match único): {written}")
    print(f"  Filas sin match / RAK genérico (quedaron en blanco): {skipped_no_match}")
    print(f"  Filas ambiguas (quedaron en blanco, revisar CSV): {skipped_ambiguous_or_generic}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
