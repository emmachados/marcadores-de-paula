"""Exercise project recovery and turn controls in the packaged desktop application."""
import json
from pathlib import Path
import tempfile
import traceback
import wave
from time import monotonic

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication

from transcript_desk.annotations import create_annotation
from transcript_desk.review_storage import atomic_write, load_review
from transcript_desk.turn_dialog import TurnDialog


def run_review_check(application, window, report_path):
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='paula-project-check-')
    window.review_check_files = temporary
    folder = Path(temporary.name)
    with wave.open(str(folder / 'audio.wav'), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * 16000 * 4)
    original = {'metadata': {'title': 'Synthetic review verification', 'media_path': r'C:\moved\audio.wav'},
                'speaker_names': {'A': 'Interviewer', 'B': 'Participant'},
                'segments': [{'start': 0, 'end': 2, 'speaker': 'A', 'text': 'Well, well.',
                              'words': [{'word': 'Well,', 'start': 0, 'end': .7, 'speaker': 'A',
                                         'speaker_review': 'multiple_speakers', 'speaker_candidates': {'A': .7, 'B': .2}},
                                        {'word': 'well.', 'start': 1, 'end': 2, 'speaker': 'A'}]},
                             {'start': 2, 'end': 4, 'speaker': 'B', 'text': 'Yes, yes.'}]}
    original['annotations'] = [create_annotation(original, 0, 6, 10, 'Repetition')]
    atomic_write(folder / 'result.json', original)
    finished = False

    def finish(success, error=''):
        nonlocal finished
        if finished:
            return
        finished = True
        window.grab().save(str(report.with_suffix('.png')))
        report.write_text(json.dumps({'success': success, 'error': error,
            'checks': ['moved project', 'split dialog', 'speaker assignment', 'undo and redo',
                       'save and reopen', 'original retained', 'audio playback',
                       'speaker review navigation', 'elapsed stage display']}), encoding='utf-8')
        # Release the Windows media handle while Qt's event loop is still alive.
        window.player.player.stop()
        window.player.player.setSource(QUrl())
        QTimer.singleShot(250, lambda: application.exit(0 if success else 1))

    def check():
        try:
            assert window.open_project_folder(folder)
            assert window.speaker_review_button.text() == 'Check speakers (1)'
            window.speaker_review_button.click()
            assert window.transcript.currentRow() == 0
            assert 'more than one speaker' in window.review_hint.text()
            now = monotonic()
            window.processing_started, window.stage_started = now - 65, now - 25
            window.refresh_processing_time()
            assert 'Elapsed 1:05' in window.processing_time.text()
            application.processEvents()
            window.grab().save(str(report.with_name(report.stem + '-speakers.png')))
            window.processing_started = None
            window.processing_time.clear()
            window.transcript.setCurrentCell(0, 3)

            def split_dialog():
                dialog = next(w for w in QApplication.topLevelWidgets() if isinstance(w, TurnDialog) and w.isVisible())
                dialog.operation.setCurrentIndex(2)
                cursor = dialog.passage.textCursor()
                cursor.setPosition(6)
                dialog.passage.setTextCursor(cursor)
                dialog.split_time.setValue(1)
                dialog.grab().save(str(report.with_name(report.stem + '-dialog.png')))
                dialog.accept()

            QTimer.singleShot(100, split_dialog)
            window.edit_turn_button.click()
            assert len(window.result['segments']) == 3
            assert window.apply_turn_edit('speaker', 1, 'B')
            window.undo_review_button.click()
            assert window.result['segments'][1]['speaker'] == 'A'
            window.redo_review_button.click()
            assert window.result['segments'][1]['speaker'] == 'B'
            assert window.save_edits()
            window.load_result(window.result_job)
            assert window.result['segments'][1]['text'] == 'well.'
            assert load_review(folder)[0]['annotations'][0]['segment'] == 1
            assert json.loads((folder / 'result.json').read_text(encoding='utf-8')) == original
            window.player.audio.setMuted(True)
            window.player.player.positionChanged.connect(lambda ms: finish(True) if ms >= 250 else None)
            window.player.player.play()
        except Exception:
            finish(False, traceback.format_exc())

    QTimer.singleShot(15000, lambda: finish(False, 'Review verification timed out.'))
    QTimer.singleShot(100, check)
