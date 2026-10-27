"""Relleno de una sola corrida: completa el motivo de los reemplazos viejos.

Los reemplazos anteriores a la función del motivo quedaron con el campo vacío.
El usuario corroboró equipo por equipo cuáles fueron refurbish y cuáles no, y
esto deja ese dato asentado en vez de perderlo.

Los dos que no se pudieron confirmar quedan vacíos a propósito: inventarles una
causa arruinaría justamente lo que el campo existe para medir.

Uso:
    python backfill_motivos.py            # muestra qué haría, no escribe
    python backfill_motivos.py --aplicar  # escribe
"""

from __future__ import annotations

import sys

from app.config import load_database_path
from app.db import get_all_replacements, get_connection

# Confirmado por el usuario el 27/10/2026 revisando equipo por equipo. Se apoya
# además en una señal independiente: todos estos tienen el RAK nuevo con prefijo
# MPC, que es el que llevan los equipos que pasan por refurbish, y los dos que
# quedan afuera no lo tienen.
MOTIVOS_CONFIRMADOS: dict[int, str] = {
    3: "refurbish",   # Cramer 3500
    4: "refurbish",   # José Luis Cantilo 5101
    5: "refurbish",   # Marcos Paz 3405
    6: "refurbish",   # Chile 2099
    7: "refurbish",   # Av. Amancio Alcorta 3531
    10: "refurbish",  # Vera 1384
}

# Sin confirmar: se dejan vacíos.
SIN_CONFIRMAR = {8: "Andalgalá 2489", 9: "Avenida Emilio Castro 6693"}


def main(aplicar: bool) -> int:
    conn = get_connection(load_database_path())
    try:
        reemplazos = {r.id: r for r in get_all_replacements(conn)}

        print("=== A COMPLETAR ===")
        a_escribir = []
        for rid, motivo in MOTIVOS_CONFIRMADOS.items():
            r = reemplazos.get(rid)
            if r is None:
                print(f"  [aviso] no existe el reemplazo id={rid}, se saltea.")
                continue
            if r.motivo:
                print(f"  ya tiene motivo ({r.motivo}): {r.nombre_pc} — se saltea.")
                continue
            if not r.new_rak.upper().startswith("MPC"):
                # Guarda de coherencia: si el RAK no es MPC, la confirmación no
                # concuerda con la señal independiente y conviene revisarlo a mano.
                print(f"  [revisar] {r.nombre_pc}: RAK nuevo '{r.new_rak}' no es MPC.")
                continue
            print(f"  id={rid:<3} {(r.nombre_pc or '?')[:30]:<30} -> {motivo}")
            a_escribir.append((rid, motivo))

        print()
        print("=== SE DEJAN VACÍOS (sin confirmar) ===")
        for rid, nombre in SIN_CONFIRMAR.items():
            r = reemplazos.get(rid)
            estado = f"motivo='{r.motivo}'" if r and r.motivo else "vacío"
            print(f"  id={rid:<3} {nombre:<30} {estado}")

        if not aplicar:
            print(f"\nSimulación: se completarían {len(a_escribir)}. Corré con --aplicar para escribir.")
            return 0

        conn.executemany("UPDATE host_replacements SET motivo = ? WHERE id = ? AND motivo IS NULL",
                         [(m, i) for i, m in a_escribir])
        conn.commit()

        print(f"\nCompletados: {len(a_escribir)}")
        print()
        print("=== ESTADO FINAL ===")
        for r in sorted(get_all_replacements(conn), key=lambda x: x.id):
            print(f"  id={r.id:<3} {(r.nombre_pc or '?')[:30]:<30} {r.motivo or '(vacío)'}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(aplicar="--aplicar" in sys.argv))
