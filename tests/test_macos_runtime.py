import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from transcript_desk import file_replace
from transcript_desk import runtime


def test_native_worker_name_in_frozen_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, 'resource_root', lambda: tmp_path)
    assert runtime.worker_command('job.json', 'cpu', False) == [str(tmp_path / 'worker/TranscriptWorker'), 'job.json']


def test_packaged_mac_installs_python_from_local_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(runtime, 'resource_root', lambda: tmp_path)
    monkeypatch.setattr(runtime, 'tool_path', lambda name: '/tools/uv')
    (tmp_path / 'requirements-engine-macos-arm64.txt').write_text('')
    mirror = tmp_path / 'python-install'
    mirror.mkdir()
    (mirror / 'manifest.json').write_text(json.dumps({'version': '3.11.17'}))
    command = runtime.setup_commands('cpu')[0]
    assert command[3] == '3.11.17'
    assert command[-2:] == ['--mirror', mirror.as_uri()]
    (mirror / 'manifest.json').unlink()
    with pytest.raises(ValueError, match='Reinstall'):
        runtime.setup_commands('cpu')


def test_macos_uses_hashed_native_lock_and_rejects_unsupported_engines(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(runtime, 'tool_path', lambda name: '/tools/uv')
    commands = runtime.setup_commands('cpu')
    assert '--require-hashes' in commands[-2]
    assert '--no-build' in commands[-2]
    assert '--torch-backend' not in commands[-2]
    assert runtime.engine_lock('cpu').name == 'requirements-engine-macos-arm64.txt'
    with pytest.raises(ValueError, match='CUDA'):
        runtime.setup_commands('cuda')
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'x86_64')
    with pytest.raises(ValueError, match='Intel'):
        runtime.setup_commands('cpu')


def test_frozen_macos_environment_separates_qt_and_exposes_bundled_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, 'resource_root', lambda: tmp_path)
    (tmp_path / 'tools').mkdir()
    monkeypatch.setenv('QT_PLUGIN_PATH', '/unrelated/qt')
    monkeypatch.setenv('DYLD_LIBRARY_PATH', '/unrelated/libs')
    env = runtime.child_environment()
    assert not env.contains('QT_PLUGIN_PATH')
    assert not env.contains('DYLD_LIBRARY_PATH')
    assert env.value('PATH').split(os.pathsep)[0] == str(tmp_path / 'tools')
    assert env.value('TRANSCRIPT_DESK_FFMPEG_LIB') == str(tmp_path / 'tools/lib')


def test_engine_child_import_does_not_change_packaged_sources(monkeypatch, tmp_path):
    package = tmp_path / 'signed-resources'
    package.mkdir()
    module = package / 'signature_fixture.py'
    module.write_text('answer = 42\n')
    monkeypatch.delenv('PYTHONDONTWRITEBYTECODE', raising=False)
    monkeypatch.delenv('PYTHONPYCACHEPREFIX', raising=False)
    monkeypatch.setattr(runtime, 'resource_root', lambda: package)
    monkeypatch.setattr(runtime, 'runtime_root', lambda: tmp_path / 'engine-state')
    environment = runtime.child_environment()
    completed = subprocess.run(
        [sys.executable, '-c', 'import signature_fixture; print(signature_fixture.answer)'],
        cwd=tmp_path, env={key: environment.value(key) for key in environment.keys()},
        capture_output=True, text=True, check=True)
    assert completed.stdout.strip() == '42'
    assert list(package.iterdir()) == [module]
    assert module.read_text() == 'answer = 42\n'


@pytest.mark.parametrize('entry', [None, {'config': {}}, {'id': 'x', 'source': 'audio', 'status': 'complete', 'config': {'output': None}}])
def test_malformed_queue_is_preserved_without_startup_exception(tmp_path, entry):
    path = tmp_path / 'queue.json'
    path.write_text(json.dumps([entry]))
    store = runtime.QueueStore(path)
    assert not store.jobs and store.load_error
    assert list(tmp_path.glob('queue-unreadable-*.json'))


def test_failed_queue_quarantine_preserves_original(monkeypatch, tmp_path):
    path = tmp_path / 'queue.json'
    path.write_text('broken')
    def fail(*args):
        raise PermissionError('Read-only folder')
    monkeypatch.setattr(Path, 'rename', fail)
    store = runtime.QueueStore(path)
    assert store.read_only and path.read_text() == 'broken'
    with pytest.raises(OSError):
        store.add('audio', 'file', {'output': str(tmp_path)})
    assert not store.jobs


def test_failed_queue_replace_keeps_last_saved_state(monkeypatch, tmp_path):
    path = tmp_path / 'queue.json'
    store = runtime.QueueStore(path)
    store.add('first.wav', 'file', {'output': str(tmp_path)})
    original = path.read_bytes()
    def fail(*args):
        raise OSError('Disk full')
    monkeypatch.setattr(file_replace.os, 'replace', fail)
    with pytest.raises(OSError):
        store.add('second.wav', 'file', {'output': str(tmp_path)})
    assert path.read_bytes() == original
    assert len(store.jobs) == 1
    assert not list(tmp_path.glob('.*.tmp'))
