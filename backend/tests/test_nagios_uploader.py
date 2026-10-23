import pytest

from app.config import NagiosSSHConfig
from app.nagios_uploader import (
    InvalidRakError,
    NagiosHostNotFoundError,
    NagiosReloadError,
    NagiosValidationError,
    derive_host_name,
    execute_add_host,
    execute_remove_host,
    execute_replace_host,
    prepare_add_host,
    prepare_remove_host,
)
from app.ssh_client import RemoteCommandResult

FAKE_SSH_CONFIG = NagiosSSHConfig(
    host="ec2.example.com", port=22, user="ec2-user", key_path="C:/fake/key.pem",
    status_dat_path="/usr/local/nagios/var/status.dat",
    hosts_cfg_path="/usr/local/nagios/etc/objects/hosts/",
    binary_path="/usr/local/nagios/bin/nagios",
    main_cfg_path="/usr/local/nagios/etc/nagios.cfg",
    reload_command="sudo systemctl reload nagios",
)

OK_VALIDATION = RemoteCommandResult(exit_status=0, stdout="Total Warnings: 0\nTotal Errors: 0\n", stderr="")
FAILED_VALIDATION = RemoteCommandResult(exit_status=1, stdout="Error: could not parse host\n", stderr="")
OK_RELOAD = RemoteCommandResult(exit_status=0, stdout="", stderr="")
FAILED_RELOAD = RemoteCommandResult(exit_status=1, stdout="", stderr="Job for nagios.service failed.\n")


def test_derive_host_name_truncates_to_15_and_normalizes():
    assert derive_host_name("tgacsl-612qwz0y2l44pkhx0mx9") == "TGACSL612QWZ0Y2"


def test_derive_host_name_raises_on_empty_alnum():
    with pytest.raises(InvalidRakError):
        derive_host_name("---   ---")


def test_prepare_add_host_generates_exact_cfg_block():
    plan = prepare_add_host("MPC1BBAWT11MY260617", ssh_config=FAKE_SSH_CONFIG)
    assert plan.host_name == "MPC1BBAWT11MY26"
    assert plan.filename == "MPC1BBAWT11MY26.cfg"
    assert plan.remote_path == "/usr/local/nagios/etc/objects/hosts/MPC1BBAWT11MY26.cfg"
    assert plan.content == "define host {\nuse windows_powershell\nhost_name MPC1BBAWT11MY26\n}\n"


def test_prepare_add_host_never_touches_ssh(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("prepare_add_host no debería tocar la red")

    import app.nagios_uploader as mod
    monkeypatch.setattr(mod, "fetch_remote_file", fail_if_called)

    plan = prepare_add_host("ANYRAK1234567890", ssh_config=FAKE_SSH_CONFIG)
    assert plan.host_name


def test_prepare_remove_host_reads_current_content(monkeypatch):
    import app.nagios_uploader as mod
    calls = []

    def fake_fetch(config, remote_path):
        calls.append(remote_path)
        return "define host {\nuse windows_powershell\nhost_name S166FCB490371Q5\n}\n"

    monkeypatch.setattr(mod, "fetch_remote_file", fake_fetch)

    plan = prepare_remove_host("S166FCB490371Q5", ssh_config=FAKE_SSH_CONFIG)
    assert plan.filename == "S166FCB490371Q5.cfg"
    assert plan.current_content is not None
    assert "S166FCB490371Q5" in plan.current_content
    assert calls == ["/usr/local/nagios/etc/objects/hosts/S166FCB490371Q5.cfg"]


def test_prepare_remove_host_when_file_missing(monkeypatch):
    import app.nagios_uploader as mod
    monkeypatch.setattr(mod, "fetch_remote_file", lambda config, remote_path: None)

    plan = prepare_remove_host("NOEXISTE", ssh_config=FAKE_SSH_CONFIG)
    assert plan.current_content is None


def test_execute_add_host_writes_validates_and_reloads(monkeypatch):
    import app.nagios_uploader as mod
    calls = []

    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path, content)))
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))

    def fake_run(cfg, command, timeout=30):
        calls.append(("run", command))
        return OK_VALIDATION if "nagios -v" in command else OK_RELOAD

    monkeypatch.setattr(mod, "run_remote_command", fake_run)

    result = execute_add_host("MPC1BBAWT11MY260617", ssh_config=FAKE_SSH_CONFIG)

    assert result.host_name == "MPC1BBAWT11MY26"
    assert [c[0] for c in calls] == ["write", "run", "run"]
    assert calls[0][1] == "/usr/local/nagios/etc/objects/hosts/MPC1BBAWT11MY26.cfg"
    assert "nagios -v" in calls[1][1]
    assert calls[2][1] == "sudo systemctl reload nagios"


def test_execute_add_host_reverts_and_raises_when_validation_fails(monkeypatch):
    import app.nagios_uploader as mod
    calls = []

    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path)))
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))
    monkeypatch.setattr(mod, "run_remote_command", lambda cfg, command, timeout=30: FAILED_VALIDATION)

    with pytest.raises(NagiosValidationError):
        execute_add_host("MPC1BBAWT11MY260617", ssh_config=FAKE_SSH_CONFIG)

    assert [c[0] for c in calls] == ["write", "delete"]  # se revirtió, nunca llegó a recargar


def test_execute_add_host_raises_when_reload_fails(monkeypatch):
    import app.nagios_uploader as mod

    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: None)
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: pytest.fail("no debería revertir: la config validó"))

    def fake_run(cfg, command, timeout=30):
        return OK_VALIDATION if "nagios -v" in command else FAILED_RELOAD

    monkeypatch.setattr(mod, "run_remote_command", fake_run)

    with pytest.raises(NagiosReloadError):
        execute_add_host("MPC1BBAWT11MY260617", ssh_config=FAKE_SSH_CONFIG)


def test_execute_remove_host_deletes_validates_and_reloads(monkeypatch):
    import app.nagios_uploader as mod
    calls = []

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: "define host {\nuse windows_powershell\nhost_name S166FCB490371Q5\n}\n")
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path, content)))

    def fake_run(cfg, command, timeout=30):
        calls.append(("run", command))
        return OK_VALIDATION if "nagios -v" in command else OK_RELOAD

    monkeypatch.setattr(mod, "run_remote_command", fake_run)

    result = execute_remove_host("S166FCB490371Q5", ssh_config=FAKE_SSH_CONFIG)

    assert result.host_name == "S166FCB490371Q5"
    assert [c[0] for c in calls] == ["delete", "run", "run"]  # nunca reescribe: la validación pasó


def test_execute_remove_host_restores_and_raises_when_validation_fails(monkeypatch):
    import app.nagios_uploader as mod
    calls = []
    original_content = "define host {\nuse windows_powershell\nhost_name S166FCB490371Q5\n}\n"

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: original_content)
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path, content)))
    monkeypatch.setattr(mod, "run_remote_command", lambda cfg, command, timeout=30: FAILED_VALIDATION)

    with pytest.raises(NagiosValidationError):
        execute_remove_host("S166FCB490371Q5", ssh_config=FAKE_SSH_CONFIG)

    assert [c[0] for c in calls] == ["delete", "write"]
    assert calls[1][2] == original_content  # restauró el contenido original exacto


def test_execute_remove_host_raises_when_host_not_found(monkeypatch):
    import app.nagios_uploader as mod
    calls = []

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: None)
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append("delete"))
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append("write"))
    monkeypatch.setattr(mod, "run_remote_command", lambda cfg, command, timeout=30: calls.append("run"))

    with pytest.raises(NagiosHostNotFoundError):
        execute_remove_host("NOEXISTE", ssh_config=FAKE_SSH_CONFIG)

    assert calls == []  # nunca toca el servidor si no hay nada que borrar


def test_execute_replace_host_removes_old_writes_new_and_reloads_once(monkeypatch):
    import app.nagios_uploader as mod
    calls = []
    old_content = "define host {\nuse windows_powershell\nhost_name ABCDEF123456789\n}\n"

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: old_content)
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path)))

    def fake_run(cfg, command, timeout=30):
        calls.append(("run", command))
        return OK_VALIDATION if "nagios -v" in command else OK_RELOAD

    monkeypatch.setattr(mod, "run_remote_command", fake_run)

    result = execute_replace_host("ABCDEF1234567890XYZ", "NUEVORAK1234567", ssh_config=FAKE_SSH_CONFIG)

    assert result.old_host_name == "ABCDEF123456789"
    assert result.new_host_name == "NUEVORAK1234567"
    assert result.old_removed is True
    # borra el viejo, escribe el nuevo, y recién entonces valida y recarga UNA vez cada cosa
    assert [c[0] for c in calls] == ["delete", "write", "run", "run"]
    assert sum(1 for c in calls if c[0] == "run" and "reload" in c[1]) == 1


def test_execute_replace_host_restores_previous_state_when_validation_fails(monkeypatch):
    import app.nagios_uploader as mod
    calls = []
    old_content = "define host {\nuse windows_powershell\nhost_name ABCDEF123456789\n}\n"

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: old_content)
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path, content)))
    monkeypatch.setattr(mod, "run_remote_command", lambda cfg, command, timeout=30: FAILED_VALIDATION)

    with pytest.raises(NagiosValidationError):
        execute_replace_host("ABCDEF1234567890XYZ", "NUEVORAK1234567", ssh_config=FAKE_SSH_CONFIG)

    # borra viejo, escribe nuevo, y al fallar: borra el nuevo y restaura el viejo con su contenido exacto
    assert [c[0] for c in calls] == ["delete", "write", "delete", "write"]
    assert calls[3][2] == old_content
    assert "ABCDEF123456789.cfg" in calls[3][1]


def test_execute_replace_host_when_old_rak_has_no_cfg(monkeypatch):
    import app.nagios_uploader as mod
    calls = []

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: None)  # el viejo nunca estuvo en Nagios
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: calls.append(("delete", path)))
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: calls.append(("write", path)))

    def fake_run(cfg, command, timeout=30):
        calls.append(("run", command))
        return OK_VALIDATION if "nagios -v" in command else OK_RELOAD

    monkeypatch.setattr(mod, "run_remote_command", fake_run)

    result = execute_replace_host("ABCDEF1234567890XYZ", "NUEVORAK1234567", ssh_config=FAKE_SSH_CONFIG)

    assert result.old_removed is False
    assert [c[0] for c in calls] == ["write", "run", "run"]  # no intenta borrar lo que no existe


def test_execute_remove_host_raises_when_reload_fails(monkeypatch):
    import app.nagios_uploader as mod

    monkeypatch.setattr(mod, "fetch_remote_file", lambda cfg, path: "contenido")
    monkeypatch.setattr(mod, "delete_remote_file", lambda cfg, path: None)
    monkeypatch.setattr(mod, "write_remote_file", lambda cfg, path, content: pytest.fail("no debería restaurar: la config validó"))

    def fake_run(cfg, command, timeout=30):
        return OK_VALIDATION if "nagios -v" in command else FAILED_RELOAD

    monkeypatch.setattr(mod, "run_remote_command", fake_run)

    with pytest.raises(NagiosReloadError):
        execute_remove_host("S166FCB490371Q5", ssh_config=FAKE_SSH_CONFIG)
