"""Administra los usuarios que pueden entrar al panel.

Uso:
    python manage_users.py listar
    python manage_users.py crear <usuario>
    python manage_users.py clave <usuario>      # cambiar la contraseña
    python manage_users.py borrar <usuario>

La contraseña se pide por teclado y no se muestra en pantalla, para que no
quede en el historial de la terminal.
"""

from __future__ import annotations

import getpass
import sys
from datetime import datetime

from app.auth import (
    UserAlreadyExistsError,
    UserNotFoundError,
    create_user,
    delete_user,
    list_users,
    set_password,
)
from app.config import load_database_path
from app.db import get_connection


def _pedir_clave() -> str:
    clave = getpass.getpass("Contraseña: ")
    if len(clave) < 8:
        print("La contraseña tiene que tener al menos 8 caracteres.")
        sys.exit(1)
    if clave != getpass.getpass("Repetir contraseña: "):
        print("Las contraseñas no coinciden.")
        sys.exit(1)
    return clave


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    accion = sys.argv[1].lower()
    usuario = sys.argv[2] if len(sys.argv) > 2 else None
    conn = get_connection(load_database_path())

    try:
        if accion == "listar":
            usuarios = list_users(conn)
            if not usuarios:
                print("No hay usuarios cargados. Creá el primero con: python manage_users.py crear <usuario>")
                return 0
            print(f"{len(usuarios)} usuario(s):")
            for u in usuarios:
                creado = datetime.fromtimestamp(u.created_at).strftime("%Y-%m-%d %H:%M")
                print(f"   {u.username:<20} (creado {creado})")
            return 0

        if not usuario:
            print(f"Falta el nombre de usuario. Uso: python manage_users.py {accion} <usuario>")
            return 1

        if accion == "crear":
            create_user(conn, usuario, _pedir_clave())
            print(f"Usuario '{usuario.strip().lower()}' creado.")
        elif accion == "clave":
            set_password(conn, usuario, _pedir_clave())
            print(f"Contraseña de '{usuario.strip().lower()}' actualizada. Sus sesiones abiertas se cerraron.")
        elif accion == "borrar":
            delete_user(conn, usuario)
            print(f"Usuario '{usuario.strip().lower()}' borrado.")
        else:
            print(f"Acción desconocida: {accion}")
            print(__doc__)
            return 1
        return 0

    except (UserAlreadyExistsError, UserNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
