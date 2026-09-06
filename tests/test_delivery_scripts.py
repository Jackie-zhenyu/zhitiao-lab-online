"""Offline delivery checks; package downloads and real servers are separate acceptance runs."""

from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from scripts import check_environment as checker
from scripts import manage


def test_partial_existing_venv_is_never_overwritten(tmp_path, capsys):
    (tmp_path / "requirements.lock.txt").write_text("pytest==9.1.1\n", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    existing = venv / "user-note.txt"
    existing.write_text("preserve this file", encoding="utf-8")
    assert manage.install(tmp_path) == 1
    assert existing.read_text(encoding="utf-8") == "preserve this file"
    assert "incomplete" in capsys.readouterr().err


def test_installer_refuses_unverified_python_before_creating_venv(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(manage.sys, "version_info", (3, 11, 9))
    assert manage.install(tmp_path) == 1
    assert not (tmp_path / ".venv").exists()
    assert "Python 3.12" in capsys.readouterr().err


def test_installer_refuses_redirected_venv_before_creating_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "requirements.lock.txt").write_text("pytest==9.1.1\n", encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == tmp_path / ".venv")
    def forbidden(*args, **kwargs):
        raise AssertionError("A redirected .venv must not create files or launch a process")
    monkeypatch.setattr(manage, "run", forbidden)
    assert manage.install(tmp_path) == 1
    assert not (tmp_path / ".venv").exists()
    assert "redirects elsewhere" in capsys.readouterr().err


def test_installer_reports_failed_pip_and_does_not_claim_success(tmp_path, monkeypatch, capsys):
    (tmp_path / "requirements.lock.txt").write_text("pytest==9.1.1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    monkeypatch.setattr(manage, "environment_check", lambda *args, **kwargs: 0)
    commands = []
    def failed_run(command, root):
        commands.append(command)
        return 1
    monkeypatch.setattr(manage, "run", failed_run)
    assert manage.install(tmp_path) == 1
    assert len(commands) == 1
    assert commands[0][0] == str(manage.venv_python(tmp_path))
    assert "--require-virtualenv" in commands[0]
    output = capsys.readouterr()
    assert "NOT complete" in output.err
    assert "Installation verified" not in output.out


def test_check_only_validates_without_launching_server(tmp_path, monkeypatch, capsys):
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(manage, "environment_check", lambda *args, **kwargs: 0)
    def forbidden(*args):
        raise AssertionError("Check-only must not launch a process")
    monkeypatch.setattr(manage, "run", forbidden)
    assert manage.start(tmp_path, port=8501, check_only=True) == 0
    assert "no server or model request" in capsys.readouterr().out


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_stops_before_environment_or_server_access(tmp_path, monkeypatch, port):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid port must stop before environment access")
    monkeypatch.setattr(manage, "environment_check", forbidden)
    assert manage.start(tmp_path, port=port, check_only=False) == 1


def test_start_uses_project_python_and_localhost_and_preserves_failure(tmp_path, monkeypatch, capsys):
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(manage, "environment_check", lambda *args, **kwargs: 0)
    commands = []
    def failed_start(command, root):
        commands.append((command, root))
        return 5
    monkeypatch.setattr(manage, "run", failed_start)
    assert manage.start(tmp_path, port=8509, check_only=False) == 5
    command, working_directory = commands[0]
    assert command[:5] == [str(manage.venv_python(tmp_path)), "-m", "streamlit", "run", "app.py"]
    assert command[command.index("--server.address") + 1] == "127.0.0.1"
    assert command[command.index("--server.port") + 1] == "8509"
    assert working_directory == tmp_path
    assert "stopped with an error" in capsys.readouterr().err


def test_process_error_does_not_print_sensitive_exception(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("private-secret-in-an-environment-error")
    monkeypatch.setattr(manage.subprocess, "run", fail)
    assert manage.run(["missing-python"], tmp_path) == 1
    assert "private-secret" not in capsys.readouterr().err


def test_runtime_check_rejects_system_packages(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("include-system-site-packages = true\n", encoding="utf-8")
    monkeypatch.setattr(checker.sys, "prefix", str(venv))
    monkeypatch.setattr(checker.sys, "base_prefix", str(tmp_path / "system"))
    assert any("isolate system packages" in error for error in checker.check_runtime(venv))


def test_dependency_check_detects_missing_and_wrong_versions(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("numpy==2.5.2\npytest==9.1.1\n", encoding="utf-8")
    def fake_version(name):
        if name == "numpy":
            return "1.0.0"
        raise checker.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(checker.metadata, "version", fake_version)
    errors = checker.check_dependencies(lock)
    assert len(errors) == 2
    assert any("version mismatch: numpy" in error for error in errors)
    assert any("Missing dependency: pytest==9.1.1" in error for error in errors)


def test_import_failure_is_clear_and_does_not_leak_exception(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("numpy==2.5.2\n", encoding="utf-8")
    monkeypatch.setattr(checker.metadata, "version", lambda name: "2.5.2")
    def fail_import(name):
        raise RuntimeError("private-secret-in-import-error")
    monkeypatch.setattr(checker.importlib, "import_module", fail_import)
    errors = checker.check_dependencies(lock)
    assert errors
    assert "Cannot import numpy" in "\n".join(errors)
    assert "private-secret" not in "\n".join(errors)


def test_direct_dependencies_are_exact_and_match_complete_lock():
    root = Path(__file__).resolve().parents[1]
    def pins(path):
        return dict(line.split("==", 1) for line in path.read_text(encoding="utf-8-sig").splitlines()
                    if line and not line.startswith(("#", "-r")))
    lock = pins(root / "requirements.lock.txt")
    for file in ("requirements.txt", "requirements-dev.txt"):
        for package, version in pins(root / file).items():
            assert lock[package] == version


def test_native_start_command_preserves_error_exit_from_another_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    if sys.platform == "win32":
        command = ["cmd.exe", "/d", "/c", str(root / "scripts/start.cmd"), "--port", "0"]
    else:
        command = [sys.executable, str(root / "scripts/manage.py"), "start", "--port", "0"]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode == 1
    assert b"Port must be between" in result.stdout + result.stderr


def test_native_install_command_preserves_failure_and_existing_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("manage.py", "check_environment.py", "install.cmd"):
        shutil.copyfile(root / "scripts" / name, scripts / name)
    (tmp_path / "requirements.lock.txt").write_text("pytest==9.1.1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    note = tmp_path / ".venv/user-note.txt"
    note.write_text("preserve me", encoding="utf-8")
    if sys.platform == "win32":
        command = ["cmd.exe", "/d", "/c", str(scripts / "install.cmd"), sys.executable]
    else:
        command = [sys.executable, str(scripts / "manage.py"), "install"]
    result = subprocess.run(command, cwd=root, capture_output=True, timeout=10)
    assert result.returncode == 1
    assert b"incomplete" in result.stdout + result.stderr
    assert note.read_text(encoding="utf-8") == "preserve me"
