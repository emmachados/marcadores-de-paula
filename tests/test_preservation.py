import copy
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from transcript_desk.preservation import create_backup, prepare_upgrade, previous_work, merge_previous_work
from transcript_desk.review_storage import read_review
from transcript_desk.runtime import QueueStore


def settings(tmp_path):
    return QSettings(str(tmp_path / 'preferences.ini'), QSettings.IniFormat)


def earlier(tmp_path):
    root, project = tmp_path / 'old-data', tmp_path / 'old-recordings' / 'recording-1'
    root.mkdir()
    project.mkdir(parents=True)
    review = json.loads((Path(__file__).parent / 'fixtures/review-0.7.json').read_text())
    from transcript_desk.annotations import fingerprint
    review['annotations'][0]['text_sha256'] = fingerprint(review['segments'][0]['text'])
    raw = json.dumps(review, indent=2).encode()
    (project / 'reviewed.json').write_bytes(raw)
    (project / 'result.json').write_bytes(raw)
    (project / 'autosave.json').write_bytes(raw)
    (project / '.revisions').mkdir()
    (project / '.revisions' / 'named.json').write_bytes(raw)
    (project / 'private-media.wav').write_bytes(b'not included')
    job = {'id': 'old-id', 'source': 'fixture.wav', 'status': 'complete',
           'config': {'source': 'fixture.wav', 'source_type': 'file', 'output': str(project),
                      'transcribe': True, 'save_audio': True, 'save_video': False}}
    (root / 'queue.json').write_text(json.dumps([job]))
    return root, project, raw, job


def test_upgrade_preserves_legacy_bytes_settings_and_named_history(tmp_path):
    root, project, raw, job = earlier(tmp_path)
    prefs = settings(tmp_path)
    prefs.setValue('theme', 'light')
    prefs.setValue('output', str(project.parent))
    prefs.setValue('HF_TOKEN', 'never-copy')
    prefs.setValue('remember_token', True)
    backup = prepare_upgrade(root, prefs)
    assert backup is not None
    assert (backup / 'projects/1/reviewed.json').read_bytes() == raw
    assert (backup / 'projects/1/.revisions/named.json').read_bytes() == raw
    assert (project / 'reviewed.json').read_bytes() == raw
    assert json.loads((backup / 'preferences.json').read_text()) == {'theme': 'light', 'output': str(project.parent)}
    assert not list(backup.rglob('*.wav'))
    assert prepare_upgrade(root, prefs) is None
    restored, preferences, missing = previous_work(backup)
    assert missing == [] and preferences['theme'] == 'light'
    assert restored[0]['config']['output'] == str(backup / 'projects/1')
    migrated = read_review(backup / 'projects/1/reviewed.json')
    assert migrated['speaker_names'] == {'SPEAKER_00': 'Participant'}
    assert migrated['annotations'][0]['note'] == 'Earlier note'
    assert migrated['annotations'][0]['status'] == 'confirmed'
    assert migrated['annotations'][0]['id'] == 'legacy-annotation'
    assert migrated['annotations'][0]['label_id']
    assert (backup / 'projects/1/reviewed.json').read_bytes() == raw
    assert prepare_upgrade(root, prefs, version='0.9.1') is not None


def test_import_merges_without_overwrite_or_source_queue_changes(tmp_path):
    root, project, raw, job = earlier(tmp_path)
    original_queue = (root / 'queue.json').read_bytes()
    jobs, _, _ = previous_work(root)
    store = QueueStore(tmp_path / 'current/queue.json')
    duplicate_id = copy.deepcopy(job)
    duplicate_id['config']['output'] = str(tmp_path / 'current-project')
    store.jobs = [duplicate_id]
    store.save()
    assert merge_previous_work(store, jobs) == (1, 0)
    assert store.jobs[1]['id'] != job['id']
    assert merge_previous_work(store, jobs) == (0, 1)
    assert len(QueueStore(store.path).jobs) == 2
    assert (root / 'queue.json').read_bytes() == original_queue
    assert (project / 'reviewed.json').read_bytes() == raw


def test_import_failed_save_retains_current_queue(tmp_path, monkeypatch):
    root, _, _, _ = earlier(tmp_path)
    store = QueueStore(tmp_path / 'current/queue.json')
    original = store.jobs
    monkeypatch.setattr(store, 'save', lambda: (_ for _ in ()).throw(OSError('fixture write failure')))
    with pytest.raises(OSError):
        merge_previous_work(store, previous_work(root)[0])
    assert store.jobs is original


def test_invalid_earlier_queue_is_never_quarantined(tmp_path):
    source = tmp_path / 'old'
    source.mkdir()
    path = source / 'queue.json'
    path.write_text('{bad queue')
    with pytest.raises(ValueError):
        previous_work(source)
    assert path.read_text() == '{bad queue'
    assert not list(source.glob('queue-unreadable*'))
    backup = create_backup(source, settings(tmp_path))
    assert (backup / 'queue.json').read_text() == '{bad queue'
    assert json.loads((backup / 'preservation.json').read_text())['queue_error']


def test_backup_failure_does_not_mark_version_or_change_source(tmp_path, monkeypatch):
    import transcript_desk.preservation as module
    root, project, raw, _ = earlier(tmp_path)
    monkeypatch.setattr(module, '_copy_file', lambda *args: (_ for _ in ()).throw(OSError('fixture disk full')))
    with pytest.raises(OSError):
        prepare_upgrade(root, settings(tmp_path))
    assert not (root / 'work-state.json').exists()
    assert (project / 'reviewed.json').read_bytes() == raw
    assert not list((root / 'preservation-backups').iterdir())


def test_import_reconnects_windows_paths_and_stops_running_job(tmp_path):
    root, project, _, job = earlier(tmp_path)
    job['config']['output'] = r'C:\Users\Earlier\recording-1'
    job['status'] = 'running'
    (root / 'queue.json').write_text(json.dumps([job]))
    jobs, _, missing = previous_work(root, project.parent)
    assert missing == []
    assert jobs[0]['config']['output'] == str(project)
    assert jobs[0]['status'] == 'interrupted'
    assert json.loads((root / 'queue.json').read_text())[0]['status'] == 'running'


def test_import_rejects_unsafe_backup_map(tmp_path):
    root, _, _, job = earlier(tmp_path)
    (root / 'preservation.json').write_text(json.dumps({'format': 'paula-preservation', 'version': 1,
                                                     'projects': {job['config']['output']: '../outside'}}))
    with pytest.raises(ValueError, match='unsafe'):
        previous_work(root)


def test_import_ui_retains_open_review_and_exports_latest_edits(qtbot, tmp_path, result, monkeypatch):
    from test_autosave import prepared
    from transcript_desk.preservation_ui import apply_previous_work, backup_work, PreviousWorkDialog
    widget, output = prepared(qtbot, tmp_path, result)
    before = widget.result_job
    widget.transcript.item(0, 3).setText('Latest edit to preserve')
    source, _, _, _ = earlier(tmp_path)
    dialog = PreviousWorkDialog(widget, source)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.import_button.isEnabled()
    assert dialog.table.rowCount() == 1
    assert apply_previous_work(widget, previous_work(source)[0]) == (1, 0)
    assert widget.result_job is before
    assert widget.result['segments'][0]['text'] == 'Latest edit to preserve'
    destination = tmp_path / 'export'
    destination.mkdir()
    monkeypatch.setattr('transcript_desk.preservation_ui.QFileDialog.getExistingDirectory', lambda *args: str(destination))
    backup_work(widget)
    backup = next(destination.iterdir())
    restored, _, _ = previous_work(backup)
    assert len(restored) == 2
    assert any(read_review(Path(job['config']['output']) / 'reviewed.json')['segments'][0]['text'] == 'Latest edit to preserve'
               for job in restored)


def test_restoring_backup_then_editing_keeps_backup_immutable(qtbot, tmp_path, monkeypatch):
    from test_window import window
    from transcript_desk.preservation_ui import apply_previous_work
    root, _, raw, _ = earlier(tmp_path)
    backup = create_backup(root, settings(tmp_path))
    widget = window(qtbot, tmp_path)
    jobs = previous_work(backup)[0]
    assert apply_previous_work(widget, jobs) == (1, 0)
    restored = Path(widget.store.jobs[0]['config']['output'])
    assert restored.is_relative_to(widget.store.path.parent / 'imported-projects')
    assert restored != backup / 'projects/1'
    assert widget.open_project_folder(restored)
    widget.transcript.item(0, 3).setText('Edited restored review')
    assert widget.save_edits()
    assert (backup / 'projects/1/reviewed.json').read_bytes() == raw
    assert read_review(restored / 'reviewed.json')['segments'][0]['text'] == 'Edited restored review'
    assert apply_previous_work(widget, previous_work(backup)[0]) == (0, 1)


def test_failed_backup_import_removes_only_new_restore_folders(tmp_path, monkeypatch):
    root, _, raw, _ = earlier(tmp_path)
    backup = create_backup(root, settings(tmp_path))
    store = QueueStore(tmp_path / 'current/queue.json')
    protected = store.path.parent / 'imported-projects/protected'
    protected.mkdir(parents=True)
    (protected / 'keep.txt').write_text('keep')
    monkeypatch.setattr(store, 'save', lambda: (_ for _ in ()).throw(OSError('fixture disk full')))
    with pytest.raises(OSError):
        merge_previous_work(store, previous_work(backup)[0])
    assert store.jobs == []
    assert list(protected.parent.iterdir()) == [protected]
    assert (protected / 'keep.txt').read_text() == 'keep'
    assert (backup / 'projects/1/reviewed.json').read_bytes() == raw


def test_backup_ignores_unsupported_typed_preferences_and_is_importable(tmp_path):
    root, _, _, _ = earlier(tmp_path)
    prefs = settings(tmp_path)
    prefs.setValue('recent_projects', ['unsupported-native-list'])
    prefs.setValue('position/old', {'row': 1})
    backup = create_backup(root, prefs)
    assert previous_work(backup)[1] == {}


def test_import_rejects_symlink_queue(tmp_path):
    root, _, _, _ = earlier(tmp_path)
    elsewhere = tmp_path / 'external.json'
    (root / 'queue.json').rename(elsewhere)
    (root / 'queue.json').symlink_to(elsewhere)
    with pytest.raises(ValueError, match='symbolic link'):
        previous_work(root)


def test_failed_startup_backup_retains_marker_and_opens_existing_review(qtbot, tmp_path, monkeypatch):
    from test_window import window
    from transcript_desk.preservation import startup_preservation
    import transcript_desk.preservation as module
    root, project, raw, _ = earlier(tmp_path)
    (root / 'work-state.json').write_text('{"application_version": "0.7.0"}')
    marker = (root / 'work-state.json').read_bytes()
    queue = (root / 'queue.json').read_bytes()
    monkeypatch.setattr(module, 'create_backup', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('fixture disk full')))
    backup, error = startup_preservation(root, settings(tmp_path))
    assert backup is None and 'disk full' in error
    monkeypatch.setenv('TRANSCRIPT_DESK_DATA', str(root))
    widget = window(qtbot, tmp_path)
    assert widget.open_project_folder(project)
    assert widget.result['segments'][0]['text'] == 'Bueno, yes.'
    assert (root / 'work-state.json').read_bytes() == marker
    assert (root / 'queue.json').read_bytes() == queue
    assert (project / 'reviewed.json').read_bytes() == raw


def test_restored_cuda_preference_uses_cpu_on_mac_without_changing_queued_settings(qtbot, tmp_path, monkeypatch):
    from test_window import window
    from transcript_desk.preservation_ui import apply_previous_work
    import transcript_desk.preservation_ui as ui
    root, _, _, job = earlier(tmp_path)
    job['config']['device'] = 'cuda'
    (root / 'queue.json').write_text(json.dumps([job]))
    source_preferences = {'device': 'cuda', 'theme': 'light'}
    monkeypatch.setattr(ui.sys, 'platform', 'darwin')
    widget = window(qtbot, tmp_path)
    assert apply_previous_work(widget, previous_work(root)[0], source_preferences) == (1, 0)
    assert widget.device == 'cpu'
    assert widget.settings.value('device') == 'cpu'
    assert widget.store.jobs[0]['config']['device'] == 'cuda'
    assert source_preferences['device'] == 'cuda'
    assert json.loads((root / 'queue.json').read_text())[0]['config']['device'] == 'cuda'
    assert 'CPU on this Mac' in widget.message.text()
