import json
import os
from pathlib import Path
import sys

import pytest

from PySide6.QtCore import QStandardPaths

from transcript_desk import runtime
from transcript_desk.setup_dialog import SetupDialog
from test_window import window


def separated_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_DESK_DATA", raising=False)
    roaming, local = tmp_path / "roaming", tmp_path / "local"
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda kind:
                        str(local if kind == QStandardPaths.AppLocalDataLocation else roaming))
    return roaming, local


def test_runtime_uses_local_storage_without_moving_the_queue(tmp_path, monkeypatch):
    roaming, local = separated_paths(tmp_path, monkeypatch)
    assert runtime.data_root() == roaming
    assert runtime.runtime_root() == local
    runtime.QueueStore().add("example", "url", {"output": str(tmp_path)})
    assert (roaming / "queue.json").exists()
    assert not (local / "queue.json").exists()
    env = runtime.child_environment()
    for key in ("UV_DATA_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_BIN_DIR", "UV_CACHE_DIR", "HF_HOME", "TORCH_HOME"):
        assert Path(env.value(key)).is_relative_to(local)


def test_incomplete_roaming_install_is_not_reused(tmp_path, monkeypatch):
    roaming, local = separated_paths(tmp_path, monkeypatch)
    old = roaming / "engine-cpu"
    python = old / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    assert runtime.engine_dir("cpu") == local / "engine-cpu"
    (old / "ready.json").write_text(json.dumps({"whisperx": runtime.ENGINE_VERSION}))
    assert runtime.engine_dir("cpu") == old
    assert runtime.engine_ready("cpu")
    monkeypatch.setenv("TRANSCRIPT_DESK_RUNTIME", str(tmp_path / "chosen runtime"))
    assert runtime.engine_dir("cpu") == tmp_path / "chosen runtime/engine-cpu"
    assert not runtime.engine_ready("cpu")


def test_private_python_does_not_register_global_executables(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "tool_path", lambda name: "/example/uv")
    command = runtime.setup_commands("cpu")[0]
    assert all(flag in command for flag in ("--no-bin", "--no-registry", "--no-config"))


def test_windows_installs_hashed_locks_and_checks_every_version(monkeypatch):
    from transcript_desk.engine_check import locked_versions
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(runtime, 'tool_path', lambda name: '/example/uv')
    for device in ('cpu', 'cuda'):
        commands = runtime.setup_commands(device)
        install = commands[-2]
        assert install[1:3] == ['pip', 'sync']
        assert '--require-hashes' in install and '--no-build' in install
        assert '--torch-backend' not in install if device == 'cuda' else 'cpu' in install
        versions = locked_versions(runtime.engine_lock(device))
        assert versions['whisperx'] == runtime.ENGINE_VERSION
        assert versions['torch'] == ('2.8.0+cpu' if device == 'cpu' else '2.8.0+cu128')
        assert versions['torchcodec'] == '0.7.0'
        assert len(versions) > 100
        assert commands[-1][-2:] == ['transcript_desk.engine_check', str(runtime.engine_lock(device))]


def test_error_448_is_visible_and_saved(qtbot, tmp_path, monkeypatch):
    import transcript_desk.setup_dialog as module
    script = tmp_path / "fail.py"
    script.write_text("print('Failed to create Python minor version link directory: os error 448', flush=True)\nraise SystemExit(2)\n")
    python = runtime.engine_python("cpu")
    python.parent.mkdir(parents=True)
    python.touch()
    (runtime.engine_dir("cpu") / "ready.json").write_text(json.dumps({"whisperx": runtime.ENGINE_VERSION}))
    monkeypatch.setattr(module, "setup_commands", lambda device: [[sys.executable, str(script)]])
    parent = window(qtbot, tmp_path)
    dialog = SetupDialog(parent)
    parent.setup_dialog = dialog
    dialog.show()
    assert dialog.engine_status.text() == "Engine installed"
    dialog.install.click()
    qtbot.waitUntil(lambda: dialog.process is None and dialog.install.isEnabled(), timeout=10000)
    assert "Installation failed" in dialog.step_status.text()
    saved = dialog.log_path.read_text()
    assert "os error 448" in saved and "Exit code: 2" in saved
    assert "untrusted mount point" in saved
    assert not runtime.engine_ready("cpu")
    assert dialog.engine_status.text() == "Engine not installed"
    assert dialog.install.text() == "Install engine"
    assert not dialog.heartbeat.isActive()
    dialog.copy_log.click()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == saved


def test_failed_to_start_does_not_leave_setup_busy(qtbot, tmp_path, monkeypatch):
    import transcript_desk.setup_dialog as module
    monkeypatch.setattr(module, "setup_commands", lambda device: [[str(tmp_path / "missing-program")]])
    parent = window(qtbot, tmp_path)
    dialog = SetupDialog(parent)
    parent.setup_dialog = dialog
    dialog.install.click()
    qtbot.waitUntil(lambda: dialog.process is None and dialog.install.isEnabled(), timeout=10000)
    assert "Could not start setup" in dialog.log_path.read_text()
    assert not runtime.engine_ready("cpu")


def test_success_writes_marker_after_last_command(qtbot, tmp_path, monkeypatch):
    import transcript_desk.setup_dialog as module
    python = runtime.engine_python("cpu")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(module, "setup_commands", lambda device: [[sys.executable, "-c", "print('First step done')"], [sys.executable, "-c", "print('Engine imports verified')"]])
    parent = window(qtbot, tmp_path)
    dialog = SetupDialog(parent)
    parent.setup_dialog = dialog
    dialog.install.click()
    assert not runtime.engine_ready("cpu")
    qtbot.waitUntil(lambda: runtime.engine_ready("cpu"), timeout=10000)
    assert "Engine installation complete" in dialog.step_status.text()
    assert "Engine imports verified" in dialog.log_path.read_text()


def test_child_python_does_not_use_an_unrelated_environment(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/unrelated/python")
    monkeypatch.setenv("VIRTUAL_ENV", "/unrelated/venv")
    monkeypatch.setenv("HF_TOKEN", "unrelated-token")
    env = runtime.child_environment()
    assert not env.contains("PYTHONHOME") and not env.contains("VIRTUAL_ENV")
    assert not env.contains("HF_TOKEN")
    assert env.value("PYTHONNOUSERSITE") == "1"


def test_windows_dll_search_is_restored_and_not_inherited(qtbot, tmp_path, monkeypatch):
    import pytest
    if sys.platform != "win32":
        pytest.skip("Requires the Windows loader")
    import ctypes
    from PySide6.QtCore import QProcess
    kernel = ctypes.WinDLL("kernel32")
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    original = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(original), original)
    kernel.SetDllDirectoryW(str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    process = QProcess()
    try:
        runtime.start_process(process, [sys.executable, "-c",
            "import ctypes; k=ctypes.WinDLL('kernel32'); b=ctypes.create_unicode_buffer(32768); k.GetDllDirectoryW(len(b),b); print(repr(b.value))"])
        qtbot.waitUntil(lambda: process.state() == QProcess.NotRunning, timeout=10000)
        assert process.exitCode() == 0
        assert bytes(process.readAllStandardOutput()).strip() == b"''"
        restored = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(restored), restored)
        assert restored.value == str(tmp_path)
    finally:
        kernel.SetDllDirectoryW(original.value or None)


def test_installation_advice_uses_the_reported_error():
    from transcript_desk.setup_errors import installation_advice
    assert "path-length" in installation_advice("failed (os error 206)")
    assert "disk is full" in installation_advice("error (os error 112)")
    assert "in use" in installation_advice("error (os error 32)")
    assert "refused file access" in installation_advice("error (os error 5)")
    assert "certificate" in installation_advice("invalid peer certificate: UnknownIssuer")
    assert "connection" in installation_advice("download timed out")
    assert "library" in installation_advice("", -1073741515)
    assert installation_advice("An unexplained installation failure") == ""


def test_runtime_override_preserves_queue_location(tmp_path, monkeypatch):
    queue_root = runtime.data_root()
    local = tmp_path / "short runtime"
    monkeypatch.setenv("TRANSCRIPT_DESK_RUNTIME", str(local))
    assert runtime.runtime_root() == local
    assert runtime.data_root() == queue_root


def test_recovery_selects_only_full_windows_python_versions(tmp_path):
    root = runtime.runtime_root() / 'python'
    for name in ('cpython-3.11.9-windows-x86_64-none', 'cpython-3.11.14-windows-x86_64-none',
                 'cpython-3.12.1-windows-x86_64-none', 'cpython-3.11-windows-x86_64-none',
                 'cpython-3.11.20-windows-aarch64-none'):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / 'python.exe').touch()
    assert runtime.extracted_windows_python() == root / 'cpython-3.11.14-windows-x86_64-none/python.exe'


def test_unrelated_python_install_failures_cannot_be_recovered(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(runtime, 'extracted_windows_python', lambda: Path(sys.executable))
    for output in ('connection timed out', 'Failed to create Python minor version link directory (os error 5)',
                   'some unrelated path failure (os error 448)'):
        assert runtime.python_link_recovery(output, tmp_path, []) is None


def test_recovery_checks_python_and_creates_environment_without_a_link(qtbot, tmp_path, monkeypatch):
    import transcript_desk.setup_dialog as module
    failure = 'Failed to create Python minor version link directory (os error 448)'
    probe = runtime.python_link_recovery
    monkeypatch.setattr(runtime, 'extracted_windows_python', lambda: Path(sys.executable))
    def recover(output, root, remaining):
        with monkeypatch.context() as context:
            context.setattr(sys, 'platform', 'win32')
            return probe(output, root, remaining)
    monkeypatch.setattr(module, 'python_link_recovery', recover)
    python = runtime.engine_python('cpu')
    monkeypatch.setattr(module, 'setup_commands', lambda device: [
        [sys.executable, '-c', f'print({failure!r}); raise SystemExit(2)'],
        ['unused-uv', 'venv', '3.11'],
        [str(python), '-c', 'import sys; print("Recovered engine:", sys.base_prefix)'],
    ])
    parent = window(qtbot, tmp_path)
    dialog = SetupDialog(parent)
    parent.setup_dialog = dialog
    dialog.install.click()
    qtbot.waitUntil(lambda: dialog.process is None and dialog.install.isEnabled(), timeout=20000)
    assert runtime.engine_ready('cpu'), dialog.log.toPlainText()
    assert dialog.recovered_python == Path(sys.executable)
    log = dialog.log_path.read_text()
    assert 'Extracted Python verified:' in log and 'Recovered engine:' in log
    assert '--without-pip --copies' in log
    assert 'unused-uv' not in log


def test_recovery_does_not_accept_an_unusable_interpreter(qtbot, tmp_path, monkeypatch):
    import transcript_desk.setup_dialog as module
    failure = 'Failed to create Python minor version link directory (os error 448)'
    original = runtime.python_link_recovery
    monkeypatch.setattr(runtime, 'extracted_windows_python', lambda: tmp_path / 'missing-python.exe')
    def recover(output, root, remaining):
        with monkeypatch.context() as context:
            context.setattr(sys, 'platform', 'win32')
            return original(output, root, remaining)
    monkeypatch.setattr(module, 'python_link_recovery', recover)
    monkeypatch.setattr(module, 'setup_commands', lambda device: [
        [sys.executable, '-c', f'print({failure!r}); raise SystemExit(2)'],
        [sys.executable, '-c', 'print("MUST NOT RUN")'],
    ])
    parent = window(qtbot, tmp_path)
    dialog = SetupDialog(parent)
    parent.setup_dialog = dialog
    dialog.install.click()
    qtbot.waitUntil(lambda: dialog.process is None and dialog.install.isEnabled(), timeout=10000)
    assert not runtime.engine_ready('cpu')
    assert 'MUST NOT RUN' not in dialog.log.toPlainText()
    assert 'Could not start setup' in dialog.log.toPlainText()


def test_save_and_close_preserves_main_window_and_both_token_modes(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import keyring
    calls = []
    monkeypatch.setattr(keyring, 'set_password', lambda *args: calls.append(('store', args)))
    monkeypatch.setattr(keyring, 'delete_password', lambda *args: calls.append(('delete', args)))
    parent = window(qtbot, tmp_path)
    parent.remember_token = False
    for remember in (False, True, False):
        def save():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, SetupDialog)
            assert callable(dialog.done)
            dialog.token.setText('test-token-not-a-credential')
            dialog.remember.setChecked(remember)
            dialog.device.setCurrentIndex(dialog.device.findData('cuda' if remember else 'cpu'))
            dialog.save_button.click()
        QTimer.singleShot(10, save)
        parent.setup_button.click()
        qtbot.waitUntil(lambda: not parent.findChildren(SetupDialog))
        assert parent.isVisible()
        assert parent.token == 'test-token-not-a-credential'
        assert parent.remember_token is remember
        assert parent.settings.value('remember_token', type=bool) is remember
        assert parent.device == ('cuda' if remember else 'cpu')
    assert [action for action, _ in calls] == ['store', 'delete']


def test_credential_failure_keeps_setup_open_and_preserves_settings(qtbot, tmp_path, monkeypatch):
    import keyring
    parent = window(qtbot, tmp_path)
    parent.token, parent.remember_token, parent.device = 'old-session-token', False, 'cpu'
    def fail(*args):
        raise RuntimeError('Credential store unavailable')
    monkeypatch.setattr(keyring, 'set_password', fail)
    dialog = SetupDialog(parent)
    dialog.show()
    dialog.token.setText('new-session-token')
    dialog.remember.setChecked(True)
    dialog.save_button.click()
    assert dialog.isVisible() and parent.isVisible()
    assert parent.token == 'old-session-token' and parent.device == 'cpu'
    assert 'credential store could not be updated' in dialog.log.toPlainText()
    dialog.remember.setChecked(False)
    dialog.save_button.click()
    assert not dialog.isVisible() and parent.isVisible()
    assert parent.token == 'new-session-token'
    dialog.deleteLater()


def broken_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_DESK_RUNTIME", str(tmp_path / "runtime"))
    python = runtime.engine_python("cpu")
    python.parent.mkdir(parents=True)
    if os.name == "nt":
        python = python.with_name("python.cmd")
        monkeypatch.setattr(runtime, "engine_python", lambda device: python)
        python.write_text("@echo error: uv trampoline failed to spawn Python child process 1>&2\r\n@exit /b 1\r\n")
    else:
        python.write_text("#!/bin/sh\necho 'error: uv trampoline failed to spawn Python child process' >&2\nexit 1\n")
        python.chmod(0o755)
    (python.parent.parent / "ready.json").write_text(json.dumps(runtime.engine_record("cpu")))
    return python


def test_engine_that_cannot_start_is_reported_and_rebuilt_by_setup(tmp_path, monkeypatch):
    broken_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "tool_path", lambda name: "/example/uv")
    assert runtime.engine_ready("cpu")
    assert "trampoline" in runtime.engine_start_error("cpu")
    assert any(command[1:2] == ["venv"] for command in runtime.setup_commands("cpu"))


def test_engine_that_starts_reports_no_error_and_keeps_its_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_DESK_RUNTIME", str(tmp_path / "runtime"))
    monkeypatch.setattr(runtime, "engine_python", lambda device: Path(sys.executable))
    monkeypatch.setattr(runtime, "tool_path", lambda name: "/example/uv")
    assert runtime.engine_start_error("cpu") == ""
