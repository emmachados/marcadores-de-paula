"""Exercise live editing and recoverable faults in the native packaged GUI."""
import copy
import json
import math
import platform
import struct
import traceback
import wave
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, qVersion
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest

from transcript_desk.exports import speaker_name


def run_editor_check(application, window, report_path):
    from transcript_desk.runtime import data_root
    from transcript_desk.review_storage import atomic_write, load_review
    import transcript_desk.review_storage as storage
    application.setQuitOnLastWindowClosed(False)

    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    folder = data_root() / 'Synthetic editor verification'
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / 'audio.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        frame = b''.join(struct.pack('<h', int(2000 * math.sin(2 * math.pi * 220 * i / 16000)))
                         for i in range(16000))
        stream.writeframes(frame * 30)
    original = {'metadata': {'title': 'Synthetic live editor check', 'media_path': str(audio)},
                'speaker_names': {'A': 'Original speaker'},
                'segments': [{'start': i * 2, 'end': i * 2 + 1.9, 'speaker': 'A',
                              'text': 'Original café 🙂 ' + str(i)} for i in range(12)]}
    atomic_write(folder / 'result.json', original)
    original_bytes = (folder / 'result.json').read_bytes()
    status = {'synthetic_fixture': True, 'platform': platform.system(), 'qt': qVersion(),
              'checks': []}
    attempts = [0]
    iterations = [0]
    real_save = storage.save_checkpoint

    def checked(name):
        status['checks'].append(name)

    def fail(exc):
        storage.save_checkpoint = real_save
        status.update(ok=False, error=str(exc), traceback=traceback.format_exc())
        report.write_text(json.dumps(status, indent=2), encoding='utf-8')
        window.player.player.stop()
        window.workspace.audio.shutdown()
        application.exit(1)

    def start():
        try:
            assert window.open_project_folder(folder)
            window.player.audio.setMuted(True)
            window.player.player.play()
            QTimer.singleShot(100, wait_playback)
        except Exception as exc:
            fail(exc)

    def wait_playback():
        try:
            attempts[0] += 1
            if window.player.player.error() != QMediaPlayer.NoError:
                raise RuntimeError(window.player.player.errorString())
            if window.player.player.position() < 100:
                if attempts[0] > 100:
                    raise RuntimeError('Native audio playback did not advance')
                QTimer.singleShot(100, wait_playback)
                return
            checked('native audio playback advances while editing')
            edit_cycle()
        except Exception as exc:
            fail(exc)

    def edit_cycle():
        try:
            iteration = iterations[0]
            row = iteration % 12
            window.transcript.setCurrentCell(row, 2)
            window.transcript.editItem(window.transcript.item(row, 2))
            application.processEvents()
            editor = window.delegate.editor
            assert editor is not None
            editor.setFocus()
            editor.setText('Speaker café 🙂 ' + str(iteration))
            QTest.keyClick(editor, Qt.Key_Return if iteration % 2 else Qt.Key_Tab)
            application.processEvents()
            window.finish_edit()
            assert speaker_name(window.result['segments'][row]['speaker'], window.result['speaker_names']) == 'Speaker café 🙂 ' + str(iteration)
            assert window.result['speaker_names']['A'] == 'Original speaker'
            window.transcript.setCurrentCell(row, 3)
            window.transcript.editItem(window.transcript.item(row, 3))
            application.processEvents()
            editor = window.delegate.editor
            assert editor is not None
            editor.setFocus()
            text = 'Edited café 🙂 ' + str(iteration) + '\nSecond line, with a repetition repetition.'
            editor.setPlainText(text)
            cursor = editor.textCursor()
            cursor.setPosition(6)
            editor.setTextCursor(cursor)
            window.highlight_position((row * 2 + .5) * 1000)
            assert editor.toPlainText() == text and editor.textCursor().position() == 6
            assert window.save_edits()
            application.processEvents()
            assert window.result['segments'][row]['text'] == text
            if iteration % 4 == 0:
                window.move_review_history(-1)
                window.move_review_history(1)
                assert window.result['segments'][row]['text'] == text
            iterations[0] += 1
            if iterations[0] < 24:
                QTimer.singleShot(35, edit_cycle)
            else:
                checked('24 speaker and multiline text commits, Tab/Return, Unicode, save and undo/redo')
                autosave_draft()
        except Exception as exc:
            fail(exc)

    def autosave_draft():
        try:
            window.transcript.setCurrentCell(0, 3)
            window.transcript.editItem(window.transcript.item(0, 3))
            application.processEvents()
            editor = window.delegate.editor
            editor.setFocus()
            editor.setPlainText('Draft survives playback and autosave 🙂')
            cursor = editor.textCursor()
            cursor.setPosition(6)
            editor.setTextCursor(cursor)
            window.autosave_timer.stop()
            window.autosave()
            assert window.review_writer.flush()
            window.complete_review_saves()
            assert load_review(folder)[0]['segments'][0]['text'] == editor.toPlainText()
            assert editor.textCursor().position() == 6 and window.delegate.editor is editor
            QTest.keyClick(editor, Qt.Key_Escape)
            application.processEvents()
            window.autosave_timer.stop()
            window.autosave()
            assert window.review_writer.flush()
            window.complete_review_saves()
            assert load_review(folder)[0]['segments'][0]['text'] == window.result['segments'][0]['text']
            checked('live draft checkpoint and Escape cancellation preserve cursor and committed text')
            recoverable_faults()
        except Exception as exc:
            fail(exc)

    def recoverable_faults():
        try:
            def disk_failure(*args, **kwargs):
                raise OSError('Synthetic disk-full failure')
            window.transcript.item(0, 3).setText('Unsaved words remain available')
            storage.save_checkpoint = disk_failure
            try:
                assert not window.save_edits()
                assert window.dirty and 'NOT saved' in window.review_hint.text()
                assert not window.close()
                assert window.result['segments'][0]['text'] == 'Unsaved words remain available'
            finally:
                storage.save_checkpoint = real_save
            assert window.save_edits()
            checked('failed save refuses close, retains edits and recovers after retry')
            expected = copy.deepcopy(window.result)
            expected_job = window.result_job
            broken = folder.parent / 'Malformed editor verification'
            broken.mkdir(exist_ok=True)
            atomic_write(broken / 'result.json', {'segments': [{'start': 0, 'end': 1, 'text': []}]})
            assert not window.open_project_folder(broken)
            assert window.result == expected and window.result_job is expected_job
            assert not window.transcript.signalsBlocked()
            checked('malformed project preserves the open review and editable table')
            window.player.player.stop()
            assert window.load_result(expected_job)
            assert window.result['segments'][0]['text'] == 'Unsaved words remain available'
            assert speaker_name(window.result['segments'][11]['speaker'], window.result['speaker_names']) == 'Speaker café 🙂 23'
            assert (folder / 'result.json').read_bytes() == original_bytes
            checked('save/reopen retains speaker and text edits with original transcript unchanged')
            window.grab().save(str(report.with_suffix('.png')))
            assert window.close()
            from transcript_desk.window import MainWindow
            reopened = MainWindow()
            reopened.show()
            assert reopened.open_project_folder(folder)
            assert not reopened.review_read_only
            assert reopened.result['segments'][0]['text'] == 'Unsaved words remain available'
            assert speaker_name(reopened.result['segments'][11]['speaker'], reopened.result['speaker_names']) == 'Speaker café 🙂 23'
            assert reopened.close()
            checked('close/reopen releases project ownership and retains saved edits')
            status.update(ok=True, version=__import__('transcript_desk').__version__)
            report.write_text(json.dumps(status, indent=2), encoding='utf-8')
            application.exit(0)
        except Exception as exc:
            fail(exc)

    QTimer.singleShot(300, start)
