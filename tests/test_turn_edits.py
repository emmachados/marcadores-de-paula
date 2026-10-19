import copy
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog

from transcript_desk import turn_edits
from transcript_desk.annotations import create_annotation, is_current, validate_annotations
from transcript_desk.review_storage import load_review
from transcript_desk.turn_dialog import TurnDialog
from test_autosave import prepared


def annotated(result):
    result['segments'][0]['text'] = 'well 🙂 well'
    result['segments'][0]['words'] = [{'word': 'well', 'start': .5, 'end': .8}]
    result['annotations'] = [create_annotation(result, 0, 0, 4, 'Discourse marker'),
                             create_annotation(result, 0, 7, 11, 'Discourse marker'),
                             create_annotation(result, 0, 0, 11, 'Repetition'),
                             create_annotation(result, 1, 0, 3, 'Other')]
    return result


def test_split_remaps_annotations_without_losing_crossing_quotes(result):
    annotated(result)
    turn_edits.split_turn(result, 0, 7, 1.1)
    assert result['segments'][0]['text'] + result['segments'][1]['text'] == 'well 🙂 well'
    assert 'words' not in result['segments'][0]
    assert result['segments'][1]['original_words']
    left, right, crossing, later = result['annotations']
    assert is_current(left, result) and is_current(right, result)
    assert right['segment'] == 1 and right['start_char'] == 0
    assert crossing['quote'] == 'well 🙂 well' and crossing['status'] == 'needs review'
    assert not is_current(crossing, result)
    assert later['segment'] == 2 and later['status'] == 'confirmed' and is_current(later, result)
    validate_annotations(result)


def test_merge_preserves_wording_and_annotation_offsets(result):
    annotated(result)
    original = copy.deepcopy(result)
    with pytest.raises(ValueError, match='same speaker'):
        turn_edits.merge_turn(result, 0)
    assert result == original
    turn_edits.assign_speaker(result, 1, 'SPEAKER_00')
    turn_edits.merge_turn(result, 0)
    assert result['segments'][0]['text'] == 'well 🙂 well Yes & no <sometimes>.'
    assert is_current(result['annotations'][3], result)
    assert result['annotations'][3]['start_char'] == 12
    validate_annotations(result)


@pytest.mark.parametrize('start,end', [(-1, 1), (2, 1), (1, float('nan')), (0, float('inf'))])
def test_bad_boundaries_do_not_mutate(result, start, end):
    original = copy.deepcopy(result)
    with pytest.raises(ValueError):
        turn_edits.set_bounds(result, 0, start, end)
    assert result == original


def test_turn_edit_undo_redo_autosave_and_original_retention(qtbot, tmp_path, result):
    annotated(result)
    widget, output = prepared(qtbot, tmp_path, result)
    original = (output / 'result.json').read_bytes()
    assert widget.apply_turn_edit('split', 0, 7, 1.1)
    assert len(widget.result['segments']) == 4
    assert widget.apply_turn_edit('speaker', 1, 'SPEAKER_01')
    assert widget.result['segments'][0]['speaker'] == 'SPEAKER_00'
    assert widget.result['segments'][1]['speaker'] == 'SPEAKER_01'
    widget.undo_review_button.click()
    assert widget.result['segments'][1]['speaker'] == 'SPEAKER_00'
    widget.undo_review_button.click()
    assert widget.result['segments'] == result['segments']
    widget.redo_review_button.click()
    widget.redo_review_button.click()
    qtbot.waitUntil(lambda: load_review(output)[0]['segments'][1]['speaker'] == 'SPEAKER_01')
    assert (output / 'result.json').read_bytes() == original
    widget.transcript.item(1, 3).setText('Edited after split')
    widget.undo_review_button.click()
    assert widget.result['segments'][1]['text'] == 'well'
    widget.undo_review_button.click()
    widget.transcript.item(0, 3).setText('A new branch')
    assert not widget.redo_review_button.isEnabled()
    assert widget.save_edits()
    widget.load_result(widget.result_job)
    assert widget.result['segments'][0]['text'] == 'A new branch'
    assert not widget.undo_review_button.isEnabled()


def test_split_dialog_handles_unicode_cursor(qtbot, result):
    annotated(result)
    dialog = TurnDialog(result, 0, 1)
    qtbot.addWidget(dialog)
    dialog.operation.setCurrentIndex(2)
    cursor = dialog.passage.textCursor()
    cursor.setPosition(8)  # Seven Python characters, eight UTF-16 units.
    dialog.passage.setTextCursor(cursor)
    assert dialog.character() == 7
    assert dialog.split_time.value() == 1


def test_closed_turn_dialog_is_disposed_before_application_shutdown(qtbot, tmp_path, result, monkeypatch):
    widget, _ = prepared(qtbot, tmp_path, result)
    widget.transcript.setCurrentCell(0, 3)
    monkeypatch.setattr(TurnDialog, 'exec', lambda self: QDialog.Rejected)
    for _ in range(3):
        widget.edit_turn_button.click()
        qtbot.waitUntil(lambda: not widget.findChildren(TurnDialog))


def test_annotation_changes_are_undoable_without_erasing_prior_text(qtbot, tmp_path, result):
    widget, output = prepared(qtbot, tmp_path, result)
    widget.transcript.item(0, 3).setText('Well, well')
    widget.result['annotations'] = [create_annotation(widget.result, 0, 0, 4, 'Discourse marker')]
    widget.annotations.changed.emit()
    widget.undo_review_button.click()
    assert not widget.result.get('annotations')
    assert widget.result['segments'][0]['text'] == 'Well, well'
    widget.redo_review_button.click()
    assert len(widget.result['annotations']) == 1


def test_typed_speaker_name_reassigns_one_turn_and_reuses_identities(result):
    result['segments'].append({'start': 5, 'end': 6, 'speaker': 'SPEAKER_01', 'text': 'Second answer.'})
    assert turn_edits.assign_speaker_name(result, 1, 'interviewer')
    assert [s.get('speaker') for s in result['segments']] == ['SPEAKER_00', 'SPEAKER_00', None, 'SPEAKER_01']
    assert result['speaker_names'] == {'SPEAKER_00': 'Interviewer', 'SPEAKER_01': 'Participant'}
    assert turn_edits.assign_speaker_name(result, 2, 'Paula')
    assert turn_edits.assign_speaker_name(result, 3, 'Paula ')
    assert result['segments'][2]['speaker'] == result['segments'][3]['speaker'] != 'SPEAKER_01'
    assert list(result['speaker_names'].values()).count('Paula') == 1
    turn_edits.merge_turn(result, 2)
    assert result['segments'][2]['text'] == 'Uncertain speaker. Second answer.'
    assert not turn_edits.assign_speaker_name(result, 2, 'Paula')
    assert turn_edits.assign_speaker_name(result, 2, 'Unassigned')
    assert result['segments'][2]['speaker'] is None


def test_typed_default_label_selects_diarized_speaker(result):
    del result['speaker_names']['SPEAKER_01']
    turn_edits.assign_speaker_name(result, 0, 'Speaker 2')
    assert result['segments'][0]['speaker'] == 'SPEAKER_01'


def test_speaker_assignment_updates_recognition_word_copies(result):
    words = [{'word': 'Uncertain', 'start': 4, 'end': 4.3, 'speaker': 'SPEAKER_01'},
             {'word': 'speaker.', 'start': 4.3, 'end': 4.6}]
    result['segments'][2]['words'] = copy.deepcopy(words)
    result['word_segments'] = copy.deepcopy(words)
    result['original_segments'] = [{'start': 4, 'end': 4.6, 'text': 'Uncertain speaker.', 'words': copy.deepcopy(words)}]
    turn_edits.split_turn(result, 2, 10, 4.3)
    turn_edits.assign_speaker(result, 3, 'SPEAKER_00')
    assert [w.get('speaker') for w in result['word_segments']] == ['SPEAKER_01', 'SPEAKER_00']
    assert [w.get('speaker') for w in result['original_segments'][0]['words']] == ['SPEAKER_01', 'SPEAKER_00']
    assert result['word_segments'][1]['original_speaker'] is None


def test_corrected_speaker_names_leave_no_unused_speakers(result):
    for typed in ('Pa', 'Pau', 'Paula'):
        turn_edits.assign_speaker_name(result, 2, typed)
    assert sorted(result['speaker_names'].values()) == ['Interviewer', 'Participant', 'Paula']
    identity = result['segments'][2]['speaker']
    result.setdefault('speaker_profiles', {})[identity] = {'color': '#336699', 'note': ''}
    turn_edits.assign_speaker_name(result, 2, 'Interviewer')
    assert result['speaker_names'][identity] == 'Paula'
