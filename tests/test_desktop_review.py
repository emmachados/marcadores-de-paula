from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from test_autosave import prepared


def test_filter_keeps_original_job_identity_and_review(qtbot, tmp_path, result):
    widget, output = prepared(qtbot, tmp_path, result)
    first = widget.store.jobs[0]
    widget.add_sources(['https://example.com/second'], 'url')
    widget.recording_search.setText('second')
    assert widget.queue.isRowHidden(0)
    assert not widget.queue.isRowHidden(1)
    assert len(widget.store.jobs) == 2
    widget.recording_search.clear()
    widget.queue.selectRow(0)
    assert widget.result_job is first
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    widget.activateWindow()
    widget.delegate.editor.setFocus()
    qtbot.wait(30)
    widget.delegate.editor.setPlainText('Well, I I mean…')
    QTest.keySequence(widget.delegate.editor, QKeySequence('Ctrl+S'))
    qtbot.waitUntil(lambda: (output / 'reviewed.json').exists())
    assert widget.result['segments'][0]['text'] == 'Well, I I mean…'


def test_speaker_review_navigation_and_manual_correction(qtbot, tmp_path, result):
    result['segments'][0]['words'] = [{'word': 'The', 'start': .5, 'end': .7,
                                     'speaker': 'SPEAKER_00', 'speaker_review': 'multiple_speakers'}]
    widget, output = prepared(qtbot, tmp_path, result)
    assert widget.speaker_review_button.text() == 'Check speakers (2)'
    assert 'more than one speaker' in widget.transcript.item(0, 2).toolTip()
    widget.transcript.setCurrentCell(0, 2)
    widget.speaker_review_button.click()
    assert widget.transcript.currentRow() == 2
    widget.speaker_review_button.click()
    assert widget.transcript.currentRow() == 0
    assert widget.apply_turn_edit('speaker', 0, 'SPEAKER_00')
    assert widget.speaker_review_button.text() == 'Check speakers (1)'
    widget.undo_review_button.click()
    assert widget.speaker_review_button.text() == 'Check speakers (2)'


def test_elapsed_clock_reports_stage_and_device_without_percentage(qtbot, tmp_path, result, monkeypatch):
    import transcript_desk.window as module
    widget, _ = prepared(qtbot, tmp_path, result)
    widget.processing_started, widget.stage_started = 100.0, 140.0
    widget.device = 'cpu'
    monkeypatch.setattr(module, 'monotonic', lambda: 165.0)
    widget.refresh_processing_time()
    assert widget.processing_time.text() == 'Elapsed 1:05 · Current stage 0:25 · CPU'


def test_speaker_cell_changes_only_its_turn(qtbot, tmp_path, result):
    result['segments'].append({'start': 5, 'end': 6, 'speaker': 'SPEAKER_00', 'text': 'The next question.'})
    widget, _ = prepared(qtbot, tmp_path, result)
    widget.transcript.item(3, 2).setText('Paula')
    qtbot.wait(10)
    assert [widget.transcript.item(row, 2).text() for row in range(4)] == ['Interviewer', 'Participant', 'Unassigned', 'Paula']
    widget.transcript.item(2, 2).setText('Paula')
    qtbot.wait(10)
    assert widget.result['segments'][2]['speaker'] == widget.result['segments'][3]['speaker']
    widget.move_review_history(-1)
    assert widget.result['segments'][2].get('speaker') is None


def test_speaker_assignment_in_long_transcript_does_not_freeze(qtbot, tmp_path, result):
    import time
    result['segments'] = [{'start': i, 'end': i + .9, 'speaker': f'SPEAKER_0{i % 3}', 'text': f'Turn {i} ' * 8}
                          for i in range(800)]
    widget, _ = prepared(qtbot, tmp_path, result)
    started = time.perf_counter()
    assert widget.apply_turn_edit('speaker', 400, 'SPEAKER_01')
    widget.transcript.item(401, 2).setText('Paula')
    qtbot.wait(10)
    # Before column measurement was deferred, this took about 20 s.
    assert time.perf_counter() - started < 3


def test_turn_edits_update_existing_table_cells(qtbot, tmp_path, result):
    from shiboken6 import getCppPointer
    widget, _ = prepared(qtbot, tmp_path, result)
    before = [getCppPointer(widget.transcript.item(row, 2))[0] for row in range(3)]
    assert widget.apply_turn_edit('speaker', 2, 'SPEAKER_01')
    widget.move_review_history(-1)
    assert [getCppPointer(widget.transcript.item(row, 2))[0] for row in range(3)] == before
    assert widget.transcript.item(2, 2).text() == 'Unassigned'
    assert widget.apply_turn_edit('split', 0, 9, 1)
    assert widget.transcript.rowCount() == 4 and widget.transcript.item(1, 3).text() == 'is "café".'
