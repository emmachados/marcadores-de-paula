from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from transcript_desk.workspace_panel import SearchDialog, SpeakerDialog
from transcript_desk.export_dialog import ExportDialog
from transcript_desk.review_storage import load_review
from test_autosave import prepared


def test_reading_panel_search_and_single_undo(qtbot, tmp_path, result):
    result['segments'].append({'start': 5, 'end': 6, 'speaker': 'SPEAKER_00', 'text': 'The next question.'})
    widget, output = prepared(qtbot, tmp_path, result)
    widget.tabs.setCurrentWidget(widget.workspace.reading)
    reading = widget.workspace.reading
    reading.speaker.setCurrentIndex(reading.speaker.findData('SPEAKER_00'))
    assert '00:00' not in reading.text.toPlainText()
    assert 'The next question.' in reading.text.toPlainText()
    dialog = SearchDialog(widget)
    dialog.query.setText('The')
    dialog.word.setChecked(True)
    dialog.replacement.setText('A')
    assert len(dialog.matches) == 2
    dialog.replace_all()
    assert widget.result['segments'][0]['text'].startswith('A word')
    assert widget.result['segments'][3]['text'] == 'A next question.'
    widget.move_review_history(-1)
    assert widget.result['segments'][0]['text'].startswith('The word')
    assert widget.result['segments'][3]['text'] == 'The next question.'
    assert widget.save_edits()
    assert load_review(output)[0]['segments'][3]['text'] == 'The next question.'
    dialog.deleteLater()


def test_speaker_profiles_and_export_preview(qtbot, tmp_path, result):
    widget, _ = prepared(qtbot, tmp_path, result)
    dialog = SpeakerDialog(widget)
    dialog.speaker.setCurrentIndex(dialog.speaker.findData('SPEAKER_00'))
    dialog.name.setText('Paula')
    dialog.notes.setPlainText('Primary interviewer')
    dialog.color.setText('#336699')
    dialog.save_details()
    assert widget.result['speaker_names']['SPEAKER_00'] == 'Paula'
    assert widget.result['speaker_profiles']['SPEAKER_00']['color'] == '#336699'
    export = ExportDialog(widget)
    export.preset.setCurrentIndex(2)
    assert 'Paula' in export.preview.toPlainText()
    assert '00:00' not in export.preview.toPlainText()
    assert export.options()['grouped']
    destination = widget.workspace.export_to(tmp_path / 'exports', ['TXT'], export.options())
    assert destination and (destination / 'transcript.txt').exists()
    export.deleteLater()
    dialog.deleteLater()


def test_waveform_decodes_and_interval_stops(qtbot, tmp_path, result):
    from test_worker import make_wav
    widget, output = prepared(qtbot, tmp_path, result)
    path = output / 'audio.wav'
    make_wav(path)
    widget.player.load(path, 'Waveform test')
    audio = widget.workspace.audio
    audio.load_waveform()
    qtbot.waitUntil(lambda: audio.process is None, timeout=15000)
    assert len(audio.wave.peaks) >= 99
    audio.play_range(.1, .6)
    qtbot.waitUntil(lambda: audio.active_interval is None, timeout=10000)
    assert widget.player.player.position() >= 550
    audio.shutdown()


def test_speaker_list_distinguishes_identities_with_one_name(qtbot, tmp_path, result):
    result['segments'][2]['speaker'] = 'MANUAL_0002'
    result['segments'].append({'start': 5, 'end': 6, 'speaker': 'MANUAL_0003', 'text': 'Later.'})
    result['speaker_names'].update(MANUAL_0002='Paula', MANUAL_0003='Paula')
    widget, _ = prepared(qtbot, tmp_path, result)
    dialog = SpeakerDialog(widget)
    labels = [dialog.speaker.itemText(i) for i in range(dialog.speaker.count())]
    assert 'Paula (MANUAL_0002)' in labels and 'Paula (MANUAL_0003)' in labels
    dialog.deleteLater()


def test_unassigned_entry_has_no_identity_suffix(qtbot, tmp_path, result):
    result['segments'][1]['speaker'] = 'MANUAL_0001'
    result['speaker_names']['MANUAL_0001'] = 'Unassigned'
    widget, _ = prepared(qtbot, tmp_path, result)
    dialog = SpeakerDialog(widget)
    labels = [dialog.speaker.itemText(i) for i in range(dialog.speaker.count())]
    assert 'Unassigned' in labels and 'Unassigned (MANUAL_0001)' in labels
    dialog.deleteLater()
