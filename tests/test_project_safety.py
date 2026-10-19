import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from transcript_desk.automatic_revisions import record_revision
from transcript_desk.diagnostics import export_diagnostics
from transcript_desk.project_archive import list_snapshots, save_snapshot
from transcript_desk.project_lock import ProjectLock
from transcript_desk.review_storage import read_review


def test_only_one_project_writer_then_release(tmp_path):
    first, second = ProjectLock(tmp_path), ProjectLock(tmp_path)
    assert first.acquire()
    assert first.acquire()
    assert not second.acquire()
    assert 'Another application' in second.message
    first.release()
    assert second.acquire()
    second.release()


def test_project_lock_recovers_after_abrupt_process_exit(tmp_path):
    code = 'import os, sys; from transcript_desk.project_lock import ProjectLock; lock=ProjectLock(sys.argv[1]); assert lock.acquire(); os._exit(23)'
    child = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr
    lease = ProjectLock(tmp_path)
    assert lease.acquire()
    lease.release()


def test_automatic_revisions_survive_restart_and_skip_unchanged_saves(tmp_path, result):
    original = record_revision(tmp_path, result)
    assert original is not None
    result['metadata']['review_saved_at'] = '2026-10-14T12:00:00+00:00'
    assert record_revision(tmp_path, result, force=True) is None
    result['segments'][0]['text'] = 'Next revision'
    assert record_revision(tmp_path, result) is None
    updated = record_revision(tmp_path, result, force=True)
    entries = list_snapshots(tmp_path)
    assert {entry[2] for entry in entries} == {original, updated}
    assert read_review(original)['segments'][0]['text'] != 'Next revision'
    assert read_review(updated)['segments'][0]['text'] == 'Next revision'


def test_retention_never_removes_named_snapshots(tmp_path, result, monkeypatch):
    import transcript_desk.automatic_revisions as module
    monkeypatch.setattr(module, 'RETAINED_REVISIONS', 2)
    named = save_snapshot(tmp_path, result, 'Keep this revision')
    for index in range(4):
        result['segments'][0]['text'] = str(index)
        record_revision(tmp_path, result, force=True)
    assert named.exists()
    assert len(list((tmp_path / '.revisions').glob('auto-*.json'))) == 2


def test_diagnostic_export_excludes_project_files_and_credentials(tmp_path, monkeypatch):
    import transcript_desk.diagnostics as module
    monkeypatch.setattr(module, '_active', None)
    root = tmp_path / 'state'
    monkeypatch.setenv('TRANSCRIPT_DESK_DATA', str(root))
    folder = root / 'diagnostics'
    folder.mkdir(parents=True)
    (folder / 'application.log').write_text('safe event\n')
    (folder / 'queue.json').write_text('private source')
    (folder / 'transcript.json').write_text('private transcript')
    output = export_diagnostics(tmp_path / 'support.zip')
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {'application.log', 'diagnostics-info.json'}
        assert archive.read('application.log') == (folder / 'application.log').read_bytes()
