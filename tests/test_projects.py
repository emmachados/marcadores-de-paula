import copy
import json
from pathlib import Path

from transcript_desk.review_storage import atomic_write, load_review
from test_window import window
from test_worker import make_wav


def test_open_moved_project_recovers_checkpoint_and_local_audio(qtbot, tmp_path, result):
    folder = tmp_path / 'Moved recording'
    folder.mkdir()
    result['metadata']['media_path'] = r'C:\Old profile\recordings\audio.wav'
    atomic_write(folder / 'result.json', result)
    reviewed = copy.deepcopy(result)
    reviewed['segments'][0]['text'] = 'Last autosaved words'
    atomic_write(folder / 'autosave.json', reviewed)
    (folder / 'reviewed.json').write_text('{invalid')
    make_wav(folder / 'audio.wav')
    widget = window(qtbot, tmp_path)
    assert widget.open_project_folder(folder)
    assert len(widget.store.jobs) == 1 and widget.result_job['imported_project']
    assert widget.result['segments'][0]['text'] == 'Last autosaved words'
    assert Path(widget.player.player.source().toLocalFile()).resolve() == (folder / 'audio.wav').resolve()
    assert 'Unreadable' in widget.review_hint.text()
    assert not widget.start_button.isEnabled()
    widget.transcript.item(0, 3).setText('New revision')
    assert widget.save_edits()
    assert load_review(folder)[0]['segments'][0]['text'] == 'New revision'
    assert json.loads((folder / 'result.json').read_text(encoding='utf-8')) == result
    assert widget.open_project_folder(folder)
    assert len(widget.store.jobs) == 1
    widget.close()
    reopened = window(qtbot, tmp_path)
    reopened.queue.selectRow(0)
    assert reopened.result['segments'][0]['text'] == 'New revision'


def test_invalid_project_keeps_current_recording(qtbot, tmp_path, result):
    from test_autosave import prepared
    widget, output = prepared(qtbot, tmp_path, result)
    before = widget.result_job
    assert not widget.open_project_folder(tmp_path / 'missing')
    assert len(widget.store.jobs) == 1 and widget.result_job is before
    assert 'no readable' in widget.message.text()


def test_open_project_loads_once_for_new_and_selected_recording(qtbot, tmp_path, result, monkeypatch):
    folder = tmp_path / 'project'
    folder.mkdir()
    atomic_write(folder / 'result.json', result)
    widget = window(qtbot, tmp_path)
    loads = []
    original_load = widget.load_result

    def record_load(job):
        loads.append(job['config']['output'])
        return original_load(job)

    monkeypatch.setattr(widget, 'load_result', record_load)
    assert widget.open_project_folder(folder)
    assert len(loads) == 1
    assert widget.open_project_folder(folder)
    assert len(loads) == 2
    assert len(widget.store.jobs) == 1
    assert widget.result['segments'][0]['text'] == result['segments'][0]['text']
