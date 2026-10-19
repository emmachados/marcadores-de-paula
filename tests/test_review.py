import json
import shutil
import wave
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QPlainTextEdit

from transcript_desk.review import ReviewPlayer, active_text
from test_window import window


def test_highlighting_repetitions_gaps_and_edited_text():
    segment = {"start": 0, "end": 4, "text": "Uh, I I mean…", "words": [
        {"word": "Uh,", "start": 0, "end": .3},
        {"word": "I", "start": .5, "end": .6},
        {"word": "I", "start": .8, "end": 1},
        {"word": "mean…", "start": 1.1, "end": 2}]}
    assert active_text([segment], .9) == (0, (6, 1))
    assert active_text([segment], .7) == (0, None)
    assert active_text([segment], 4) == (-1, None)
    segment["text_edited"] = True
    assert active_text([segment], .9) == (0, None)


def test_real_media_plays_seeks_and_changes_rate(qtbot, tmp_path):
    path = tmp_path / "recording.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\0\0' * 16000 * 5)
    widget = ReviewPlayer()
    qtbot.addWidget(widget)
    widget.audio.setMuted(True)
    widget.load(path)
    qtbot.waitUntil(lambda: widget.player.duration() >= 4900, timeout=15000)
    widget.play.click()
    qtbot.waitUntil(lambda: widget.player.position() > 200, timeout=15000)
    widget.play.click()
    assert widget.player.playbackState() == QMediaPlayer.PausedState
    widget.player.setPosition(3500)
    qtbot.waitUntil(lambda: widget.player.position() >= 3400)
    widget.back.click()
    assert 400 <= widget.player.position() <= 650
    widget.rate.setCurrentIndex(1)
    assert widget.player.playbackRate() == .75
    widget.player.stop()


def test_active_editor_save_and_word_highlight(qtbot, tmp_path):
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path))
    widget.add_sources(['https://example.com/interview'], 'url')
    job = widget.store.jobs[0]
    job['status'] = 'complete'
    output = Path(job['config']['output'])
    output.mkdir()
    result = {'segments': [{'start': 0, 'end': 3, 'text': 'Well, I I mean, uh…',
        'words': [{'word': 'Well,', 'start': 0, 'end': .5}, {'word': 'I', 'start': .6, 'end': .8},
                  {'word': 'I', 'start': .9, 'end': 1}, {'word': 'mean,', 'start': 1.2, 'end': 1.5},
                  {'word': 'uh…', 'start': 2, 'end': 2.8}]}]}
    (output / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    widget.refresh_queue()
    widget.queue.selectRow(0)
    widget.highlight_position(950)
    assert widget.delegate.span == (8, 1)
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    editor = widget.transcript.findChild(QPlainTextEdit)
    assert editor is not None
    wording = 'Well, I I, um, I mean…\n  you know?'
    editor.setPlainText(wording)
    editor.setFocus()
    qtbot.wait(20)
    widget.save_review.click()
    saved = json.loads((output / 'reviewed.json').read_text(encoding='utf-8'))
    assert saved['segments'][0]['text'] == wording
    assert saved['segments'][0]['original_words'] == result['segments'][0]['words']
    assert json.loads((output / 'result.json').read_text(encoding='utf-8')) == result
    assert not list(output.glob('*.txt'))
    widget.highlight_position(950)
    assert widget.delegate.active_row == 0 and widget.delegate.span is None
    widget.toggle_theme()
    widget.toggle_theme()
    assert widget.transcript.item(0, 3).text() == wording


def test_draft_pipeline_retains_audio_and_defers_exports(tmp_path, monkeypatch):
    import transcript_desk.worker as worker
    from test_worker import make_wav
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg not installed')
    source = tmp_path / 'recording.wav'
    make_wav(source)
    output = tmp_path / 'draft'
    wording = 'Um, I I was, well, you know…'
    def transcribe(config, analysis, metadata, directory, checkpoints=None):
        assert analysis.exists()
        return {'metadata': metadata, 'segments': [{'start': 0, 'end': 1, 'text': wording}]}
    monkeypatch.setattr(worker, 'transcribe', transcribe)
    # Simulate a URL download so the temporary source will be removed.
    def acquire(config, work):
        path = work / 'download.wav'
        shutil.copyfile(source, path)
        return path, 'Interview'
    monkeypatch.setattr(worker, 'acquire', acquire)
    worker.run({'source': 'https://example.com/interview', 'source_type': 'url',
        'model': 'large-v3-turbo', 'device': 'cpu', 'output': str(output), 'ffmpeg': ffmpeg,
        'transcribe': True, 'diarize': False, 'save_audio': False, 'save_video': False,
        'formats': ['TXT', 'JSON'], 'review_before_export': True})
    draft = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    assert draft['segments'][0]['text'] == wording
    assert Path(draft['metadata']['media_path']).read_bytes().startswith(b'fLaC')
    assert not list(output.glob('*.txt'))
    assert not list(output.glob('.work-*'))


def test_closing_during_text_edit_saves_current_wording(qtbot, tmp_path, result):
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path))
    widget.add_sources(['https://example.com/interview'], 'url')
    job = widget.store.jobs[0]
    job['status'] = 'complete'
    output = Path(job['config']['output'])
    output.mkdir()
    (output / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    widget.refresh_queue()
    widget.queue.selectRow(0)
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    editor = widget.transcript.findChild(QPlainTextEdit)
    editor.setFocus()
    qtbot.wait(20)
    editor.setPlainText('Well, um, I I mean…')
    widget.close()
    saved = json.loads((output / 'reviewed.json').read_text(encoding='utf-8'))
    assert saved['segments'][0]['text'] == 'Well, um, I I mean…'


def test_export_dialog_writes_reviewed_wording(qtbot, tmp_path, result, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
    from PySide6.QtGui import QDesktopServices
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path))
    widget.add_sources(['https://example.com/interview'], 'url')
    job = widget.store.jobs[0]
    job['status'] = 'complete'
    output = Path(job['config']['output'])
    output.mkdir()
    (output / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    widget.refresh_queue()
    widget.queue.selectRow(0)
    wording = 'Um, well, I I mean… you know?'
    widget.transcript.item(0, 3).setText(wording)
    export_dir = tmp_path / 'exports'
    export_dir.mkdir()
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(export_dir))
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda *args: True)
    def accept():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        from PySide6.QtWidgets import QDialogButtonBox
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
    QTimer.singleShot(20, accept)
    widget.export_review()
    folder = next(export_dir.iterdir())
    assert len(list(folder.iterdir())) == 8
    assert wording in (folder / 'transcript.txt').read_text(encoding='utf-8')
    exported = json.loads((folder / 'transcript.json').read_text(encoding='utf-8'))
    assert exported['segments'][0]['text'] == wording
    assert 'review_saved_at' in exported['metadata']


def test_background_completion_keeps_active_review_editor(qtbot, tmp_path, result):
    from types import SimpleNamespace
    from PySide6.QtCore import QProcess
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path))
    widget.add_sources(['https://example.com/one', 'https://example.com/two'], 'url')
    first, second = widget.store.jobs
    first['status'] = 'complete'
    output = Path(first['config']['output'])
    output.mkdir()
    (output / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    widget.refresh_queue()
    widget.queue.selectRow(0)
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    editor = widget.transcript.findChild(QPlainTextEdit)
    editor.setFocus()
    qtbot.wait(20)
    editor.setPlainText('Well, um, I I mean…')
    widget.active = second
    second.update(status='complete', message='Complete')
    widget.process = SimpleNamespace(readAllStandardOutput=lambda: b'',
        readAllStandardError=lambda: b'', deleteLater=lambda: None)
    widget.job_finished(0, QProcess.NormalExit)
    assert editor.isVisible()
    assert editor.toPlainText() == 'Well, um, I I mean…'
    widget.save_edits()
    assert json.loads((output / 'reviewed.json').read_text(encoding='utf-8'))['segments'][0]['text'] == 'Well, um, I I mean…'
