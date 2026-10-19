import copy
import threading

from PySide6.QtCore import QTimer

from test_autosave import prepared
from test_window import window
from transcript_desk.annotations import create_annotation
from transcript_desk.review_storage import load_review


def test_slow_autosave_keeps_gui_responsive_and_newer_edits_dirty(qtbot, tmp_path, result, monkeypatch):
    import transcript_desk.review_storage as storage
    widget, folder = prepared(qtbot, tmp_path, result)
    started, release = threading.Event(), threading.Event()
    original = storage.save_checkpoint
    calls = []

    def slow(folder, document, automatic=False):
        calls.append(document['segments'][0]['text'])
        if len(calls) == 1:
            started.set()
            assert release.wait(10)
        original(folder, document, automatic)

    monkeypatch.setattr(storage, 'save_checkpoint', slow)
    widget.transcript.item(0, 3).setText('Revision A')
    widget.autosave()
    widget.autosave_timer.stop()
    try:
        qtbot.waitUntil(started.is_set)
        responsive = []
        QTimer.singleShot(0, lambda: responsive.append(True))
        qtbot.waitUntil(lambda: bool(responsive))
        widget.transcript.item(0, 3).setText('Revision B')
        widget.autosave_timer.stop()
        release.set()
        qtbot.waitUntil(lambda: not widget.pending_review_saves)
        assert widget.dirty
        assert load_review(folder)[0]['segments'][0]['text'] == 'Revision A'
        assert widget.save_edits()
        assert not widget.dirty
        assert load_review(folder)[0]['segments'][0]['text'] == 'Revision B'
    finally:
        release.set()


def test_second_window_reads_without_overwriting_and_reacquires(qtbot, tmp_path, result):
    first, folder = prepared(qtbot, tmp_path, result)
    second = window(qtbot, tmp_path)
    assert second.open_project_folder(folder)
    assert second.review_read_only
    original = copy.deepcopy(second.result)
    assert not second.apply_turn_edit('speaker', 0, 'new')
    second.annotations.find_candidates()
    assert second.result == original
    assert first.save_edits()
    assert first.close()
    assert second.open_project_folder(folder)
    assert not second.review_read_only
    assert second.apply_turn_edit('speaker', 0, 'new')
    assert second.save_edits()


def test_pending_annotation_note_saved_when_switching_immediately(qtbot, tmp_path, result):
    result['annotations'] = [create_annotation(result, 0, 0, 3, 'Other')]
    widget, folder = prepared(qtbot, tmp_path, result)
    widget.tabs.setCurrentWidget(widget.annotations)
    widget.annotations.select_id(widget.result['annotations'][0]['id'])
    widget.annotations.note.setPlainText('A note written just before switching')
    widget.add_sources(['https://example.com/second-note'], 'url')
    widget.queue.selectRow(1)
    assert load_review(folder)[0]['annotations'][0]['note'] == 'A note written just before switching'
