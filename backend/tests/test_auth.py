import time

import pytest

from app.auth import (
    SESSION_TTL_SECONDS,
    UserAlreadyExistsError,
    UserNotFoundError,
    authenticate,
    count_users,
    create_session,
    create_user,
    delete_session,
    delete_user,
    get_session_user,
    hash_password,
    list_users,
    purge_expired_sessions,
    set_password,
    verify_password,
)
from app.db import get_connection


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "test.db"))
    yield c
    c.close()


def test_hash_no_guarda_la_contrasena_en_claro():
    almacenado = hash_password("secreta123")
    assert "secreta123" not in almacenado
    assert almacenado.startswith("pbkdf2_sha256$")


def test_hash_distinto_cada_vez_por_el_salt():
    """Dos usuarios con la misma contraseña no comparten hash."""
    assert hash_password("misma") != hash_password("misma")


def test_verify_password():
    almacenado = hash_password("correcta")
    assert verify_password("correcta", almacenado) is True
    assert verify_password("incorrecta", almacenado) is False


def test_verify_password_tolera_hash_corrupto():
    assert verify_password("x", "basura") is False
    assert verify_password("x", "") is False


def test_create_and_authenticate_user(conn):
    create_user(conn, "Enzo", "clave-segura")
    assert authenticate(conn, "enzo", "clave-segura") is True
    assert authenticate(conn, "enzo", "otra") is False


def test_username_no_distingue_mayusculas(conn):
    create_user(conn, "Facundo", "clave")
    assert authenticate(conn, "FACUNDO", "clave") is True
    assert authenticate(conn, " facundo ", "clave") is True


def test_authenticate_usuario_inexistente(conn):
    assert authenticate(conn, "nadie", "clave") is False


def test_create_user_duplicado(conn):
    create_user(conn, "enzo", "clave")
    with pytest.raises(UserAlreadyExistsError):
        create_user(conn, "enzo", "otra")


def test_create_user_rechaza_vacios(conn):
    with pytest.raises(ValueError):
        create_user(conn, "", "clave")
    with pytest.raises(ValueError):
        create_user(conn, "enzo", "")


def test_set_password(conn):
    create_user(conn, "enzo", "vieja")
    set_password(conn, "enzo", "nueva")
    assert authenticate(conn, "enzo", "vieja") is False
    assert authenticate(conn, "enzo", "nueva") is True


def test_set_password_usuario_inexistente(conn):
    with pytest.raises(UserNotFoundError):
        set_password(conn, "nadie", "x")


def test_cambiar_contrasena_cierra_las_sesiones(conn):
    """Si alguien cambia su clave, las sesiones abiertas dejan de servir."""
    create_user(conn, "enzo", "vieja")
    token = create_session(conn, "enzo")
    assert get_session_user(conn, token) == "enzo"

    set_password(conn, "enzo", "nueva")
    assert get_session_user(conn, token) is None


def test_delete_user_y_sus_sesiones(conn):
    create_user(conn, "enzo", "clave")
    token = create_session(conn, "enzo")
    delete_user(conn, "enzo")
    assert authenticate(conn, "enzo", "clave") is False
    assert get_session_user(conn, token) is None


def test_delete_user_inexistente(conn):
    with pytest.raises(UserNotFoundError):
        delete_user(conn, "nadie")


def test_list_and_count_users(conn):
    create_user(conn, "enzo", "a")
    create_user(conn, "facundo", "b")
    assert count_users(conn) == 2
    assert [u.username for u in list_users(conn)] == ["enzo", "facundo"]


def test_session_valida_y_cierre(conn):
    create_user(conn, "enzo", "clave")
    token = create_session(conn, "enzo")
    assert get_session_user(conn, token) == "enzo"

    delete_session(conn, token)
    assert get_session_user(conn, token) is None


def test_session_token_invalido(conn):
    assert get_session_user(conn, "token-inventado") is None
    assert get_session_user(conn, None) is None
    assert get_session_user(conn, "") is None


def test_session_vencida_no_sirve(conn):
    create_user(conn, "enzo", "clave")
    token = create_session(conn, "enzo")
    # Se fuerza el vencimiento hacia atrás en el tiempo
    conn.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (int(time.time()) - 10, token))
    conn.commit()
    assert get_session_user(conn, token) is None


def test_purge_expired_sessions(conn):
    create_user(conn, "enzo", "clave")
    vigente = create_session(conn, "enzo")
    vencida = create_session(conn, "enzo")
    conn.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (int(time.time()) - 10, vencida))
    conn.commit()

    assert purge_expired_sessions(conn) == 1
    assert get_session_user(conn, vigente) == "enzo"


def test_dos_sesiones_del_mismo_usuario_conviven(conn):
    """Poder entrar desde la compu y el celular a la vez."""
    create_user(conn, "enzo", "clave")
    t1 = create_session(conn, "enzo")
    t2 = create_session(conn, "enzo")
    assert t1 != t2
    assert get_session_user(conn, t1) == "enzo"
    assert get_session_user(conn, t2) == "enzo"


def test_session_dura_lo_configurado(conn):
    create_user(conn, "enzo", "clave")
    token = create_session(conn, "enzo")
    row = conn.execute("SELECT created_at, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
    assert row[1] - row[0] == SESSION_TTL_SECONDS
