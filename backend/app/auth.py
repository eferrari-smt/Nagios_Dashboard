"""Usuarios y sesiones del panel.

Hasta ahora el panel no tenía acceso restringido porque solo corría en la red
interna. Al exponerlo fuera hace falta una puerta: la herramienta da de baja
hosts de Nagios y edita el inventario, así que no puede quedar abierta a quien
llegue a la URL.

Se usa solo la biblioteca estándar (`pbkdf2_hmac` para las contraseñas,
`secrets` para los tokens) para no sumar dependencias por algo que el stdlib
ya resuelve bien.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ALGORITMO = "pbkdf2_sha256"
_ITERACIONES = 240_000
SESSION_COOKIE = "panel_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 días: es una herramienta de uso diario


class UserAlreadyExistsError(RuntimeError):
    """Ya existe un usuario con ese nombre."""


class UserNotFoundError(RuntimeError):
    """No existe ese usuario."""


@dataclass(frozen=True)
class User:
    username: str
    created_at: int


def hash_password(password: str) -> str:
    """Devuelve `algoritmo$iteraciones$salt$hash`, con un salt propio por contraseña."""
    salt = secrets.token_bytes(16)
    derivado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERACIONES)
    return f"{_ALGORITMO}${_ITERACIONES}${salt.hex()}${derivado.hex()}"


def verify_password(password: str, almacenado: str) -> bool:
    try:
        algoritmo, iteraciones, salt_hex, hash_hex = almacenado.split("$")
        if algoritmo != _ALGORITMO:
            return False
        derivado = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iteraciones)
        )
    except (ValueError, TypeError):
        return False
    # compare_digest evita filtrar por tiempo de comparación cuál es el prefijo correcto.
    return hmac.compare_digest(derivado.hex(), hash_hex)


def create_user(conn: sqlite3.Connection, username: str, password: str) -> User:
    username = username.strip().lower()
    if not username or not password:
        raise ValueError("Usuario y contraseña no pueden estar vacíos.")
    ahora = int(time.time())
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), ahora),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise UserAlreadyExistsError(f"El usuario '{username}' ya existe.") from exc
    return User(username=username, created_at=ahora)


def set_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    username = username.strip().lower()
    cur = conn.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username)
    )
    conn.commit()
    if cur.rowcount == 0:
        raise UserNotFoundError(f"No existe el usuario '{username}'.")
    # Cambiar la contraseña cierra las sesiones abiertas de esa persona.
    conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    username = username.strip().lower()
    cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    if cur.rowcount == 0:
        raise UserNotFoundError(f"No existe el usuario '{username}'.")


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT username, created_at FROM users ORDER BY username").fetchall()
    return [User(username=r[0], created_at=r[1]) for r in rows]


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> bool:
    row = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    if row is None:
        # Se verifica igual contra un hash descartable para que un usuario
        # inexistente tarde lo mismo que uno con la contraseña equivocada.
        verify_password(password, hash_password("descartable"))
        return False
    return verify_password(password, row[0])


def create_session(conn: sqlite3.Connection, username: str) -> str:
    token = secrets.token_urlsafe(32)
    ahora = int(time.time())
    conn.execute(
        "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, username.strip().lower(), ahora, ahora + SESSION_TTL_SECONDS),
    )
    conn.commit()
    return token


def get_session_user(conn: sqlite3.Connection, token: str | None) -> str | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT username, expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        return None
    if row[1] < int(time.time()):
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    return row[0]


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def purge_expired_sessions(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    conn.commit()
    return cur.rowcount
