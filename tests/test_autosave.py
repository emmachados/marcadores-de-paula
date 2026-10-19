import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

from transcript_desk.exports import speaker_name
from transcript_desk.review_storage import atomic_write, load_review, save_checkpoint
from test_window import window


def prepared(qtbot, tmp_path, result):
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path))
    widget.add_sources(['https://example.com/review'], 'url')
    job = widget.store.jobs[0]
    job['status'] = 'complete'
    output = Path(job['config']['output'])
    output.mkdir()
    atomic_write(output / 'result.json', result)
    widget.refresh_queue()
    widget.queue.selectRow(0)
    assert widget.autosave_timer.interval() == 2000
    widget.autosave_timer.setInterval(40)
    return widget, output


def test_live_text_autosaves_without_committing_or_moving_cursor(qtbot, tmp_path, result):
    widget, output = prepared(qtbot, tmp_path, result)
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    editor = widget.delegate.editor
    editor.setFocus()
    editor.setPlainText('Well, um, I I mean…')
    cursor = editor.textCursor()
    cursor.setPosition(6)
    editor.setTextCursor(cursor)
    qtbot.waitUntil(lambda: (output / 'autosave.json').exists())
    assert editor.hasFocus()
    assert editor.textCursor().position() == 6
    assert editor.isVisible()
    saved, path, failures = load_review(output)
    assert saved['segments'][0]['text'] == 'Well, um, I I mean…'
    assert widget.result['segments'][0]['text'] == result['segments'][0]['text']
    assert json.loads((output / 'result.json').read_text(encoding='utf-8')) == result
    # Escape cancels the edit, including the checkpoint of that provisional text.
    qtbot.keyClick(editor, Qt.Key_Escape)
    qtbot.waitUntil(lambda: load_review(output)[0]['segments'][0]['text'] == result['segments'][0]['text'])


def test_live_speaker_name_autosaves(qtbot, tmp_path, result):
    widget, output = prepared(qtbot, tmp_path, result)
    widget.transcript.setCurrentCell(0, 2)
    widget.transcript.editItem(widget.transcript.item(0, 2))
    editor = widget.delegate.editor
    assert isinstance(editor, QLineEdit)
    editor.setText('Paula')
    qtbot.waitUntil(lambda: (output / 'autosave.json').exists())
    saved = load_review(output)[0]
    assert speaker_name(saved['segments'][0]['speaker'], saved['speaker_names']) == 'Paula'
    assert saved['speaker_names']['SPEAKER_00'] == 'Interviewer'


def test_checkpoint_recovers_previous_valid_file_and_honours_manual_save(tmp_path, result):
    first = copy.deepcopy(result)
    first['metadata']['review_saved_at'] = '2026-10-12T10:00:00+00:00'
    first['segments'][0]['text'] = 'First revision'
    second = copy.deepcopy(first)
    second['metadata']['review_saved_at'] = '2026-10-12T10:00:02+00:00'
    second['segments'][0]['text'] = 'Second revision'
    save_checkpoint(tmp_path, first, automatic=True)
    save_checkpoint(tmp_path, second, automatic=True)
    assert load_review(tmp_path)[0]['segments'][0]['text'] == 'Second revision'
    (tmp_path / 'autosave.json').write_text('{broken', encoding='utf-8')
    recovered, path, failures = load_review(tmp_path)
    assert recovered['segments'][0]['text'] == 'First revision'
    assert path.name == 'autosave.previous.json'
    assert failures == ['autosave.json']
    second['metadata']['review_saved_at'] = '2026-10-12T10:00:03+00:00'
    save_checkpoint(tmp_path, second)
    assert load_review(tmp_path)[1].name == 'reviewed.json'


def test_failed_autosave_keeps_last_checkpoint_and_retries(qtbot, tmp_path, result, monkeypatch):
    import transcript_desk.review_storage as storage
    widget, output = prepared(qtbot, tmp_path, result)
    widget.transcript.item(0, 3).setText('First revision')
    qtbot.waitUntil(lambda: (output / 'autosave.json').exists())
    original = storage.atomic_write
    def fail(path, data):
        if Path(path).name == 'autosave.json':
            raise OSError('Disk unavailable')
        original(path, data)
    monkeypatch.setattr(storage, 'atomic_write', fail)
    widget.transcript.item(0, 3).setText('Second revision')
    qtbot.waitUntil(lambda: 'NOT saved' in widget.review_hint.text())
    assert widget.dirty
    assert load_review(output)[0]['segments'][0]['text'] == 'First revision'
    monkeypatch.setattr(storage, 'atomic_write', original)
    qtbot.waitUntil(lambda: load_review(output)[0]['segments'][0]['text'] == 'Second revision')
    qtbot.waitUntil(lambda: not widget.dirty)


def test_abrupt_exit_recovers_uncommitted_editor(qtbot, tmp_path, result):
    script = tmp_path / 'crash.py'
    script.write_text('''import json, os, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from transcript_desk.window import MainWindow
app = QApplication([])
w = MainWindow()
w.output.setText(sys.argv[1])
w.add_sources(['https://example.com/crash'], 'url')
job = w.store.jobs[0]
job['status'] = 'complete'
p = Path(job['config']['output'])
p.mkdir(parents=True)
(p / 'result.json').write_text(json.dumps({'segments':[{'start':0,'end':2,'text':'Original'}]}), encoding='utf-8')
w.store.save()
w.refresh_queue()
w.queue.selectRow(0)
w.show()
w.autosave_timer.setInterval(30)
w.transcript.setCurrentCell(0,3)
w.transcript.editItem(w.transcript.item(0,3))
w.delegate.editor.setPlainText('Uncommitted words survive')
def crash():
    if (p / 'autosave.json').exists():
        os._exit(23)
    QTimer.singleShot(30, crash)
QTimer.singleShot(60, crash)
app.exec()
''', encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': str(Path.cwd()), 'QT_QPA_PLATFORM': 'offscreen'}
    completed = subprocess.run([sys.executable, str(script), str(tmp_path / 'recordings')], env=env,
                               capture_output=True, timeout=20)
    assert completed.returncode == 23, completed.stderr.decode(errors='replace')
    widget = window(qtbot, tmp_path)
    widget.queue.selectRow(0)
    assert widget.result['segments'][0]['text'] == 'Uncommitted words survive'
    assert 'Recovered' in widget.review_hint.text()


def test_switching_recordings_keeps_pending_edits_with_correct_job(qtbot, tmp_path, result):
    widget, first = prepared(qtbot, tmp_path, result)
    widget.add_sources(['https://example.com/second'], 'url')
    other = widget.store.jobs[1]
    other['status'] = 'complete'
    second = Path(other['config']['output'])
    second.mkdir()
    atomic_write(second / 'result.json', result)
    widget.refresh_queue()
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    widget.delegate.editor.setPlainText('Belongs to first recording')
    widget.queue.selectRow(1)
    assert load_review(first)[0]['segments'][0]['text'] == 'Belongs to first recording'
    widget.transcript.item(0, 3).setText('Belongs to second recording')
    qtbot.waitUntil(lambda: (second / 'autosave.json').exists())
    assert load_review(first)[0]['segments'][0]['text'] == 'Belongs to first recording'
    assert load_review(second)[0]['segments'][0]['text'] == 'Belongs to second recording'
