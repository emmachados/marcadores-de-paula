import copy
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QProcess

from transcript_desk.diagnostics import exception_record
from transcript_desk.review_history import snapshot
from transcript_desk.review_storage import atomic_write, read_review
from test_autosave import prepared


@pytest.mark.parametrize('change', [
    lambda r: r['segments'][0].update(speaker=123),
    lambda r: r['segments'][0].update(words='wrong'),
    lambda r: r['segments'][0].update(words=[None]),
    lambda r: r['segments'][0].update(words=[{'word': 'x', 'speaker_review': []}]),
    lambda r: r['segments'][0].update(words=[{'word': 'x', 'start': 'nan'}]),
    lambda r: r['speaker_names'].update(SPEAKER_00=['wrong']),
    lambda r: r['metadata'].update(title=42),
    lambda r: r.update(review_schema=99),
])
def test_malformed_review_is_rejected_before_ui_use(tmp_path, result, change):
    change(result)
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(result), encoding='utf-8')
    with pytest.raises(ValueError):
        read_review(path)


def test_legacy_numeric_timings_are_normalized(tmp_path, result):
    result['segments'][0].update(start='0.5', end='1.8', words=[{'word': 'The', 'start': '0.5', 'end': '.8'}])
    path = tmp_path / 'review.json'
    atomic_write(path, result)
    loaded = read_review(path)
    assert loaded['segments'][0]['start'] == .5
    assert loaded['segments'][0]['words'][0]['end'] == .8


def test_bad_selected_project_preserves_open_review(qtbot, tmp_path, result):
    widget, _ = prepared(qtbot, tmp_path, result)
    original_job = widget.result_job
    original = copy.deepcopy(widget.result)
    widget.add_sources(['https://example.com/broken'], 'url')
    bad = widget.store.jobs[-1]
    bad['status'] = 'complete'
    from pathlib import Path
    folder = Path(bad['config']['output'])
    folder.mkdir()
    malformed = copy.deepcopy(result)
    malformed['segments'][0]['speaker'] = 5
    atomic_write(folder / 'result.json', malformed)
    widget.refresh_queue()
    widget.queue.selectRow(1)
    assert widget.result_job is original_job
    assert widget.result == original
    assert widget.selected() is original_job
    assert not widget.transcript.signalsBlocked()


def test_background_queue_save_failure_still_cleans_process(qtbot, tmp_path, result, monkeypatch):
    widget, _ = prepared(qtbot, tmp_path, result)
    widget.active = widget.result_job
    widget.running = True
    disposed = []
    widget.process = SimpleNamespace(readAllStandardOutput=lambda: b'', readAllStandardError=lambda: b'',
                                     deleteLater=lambda: disposed.append(True))
    with monkeypatch.context() as patch:
        patch.setattr(widget.store, 'save', lambda: (_ for _ in ()).throw(OSError('Disk unavailable')))
        widget.job_finished(0, QProcess.NormalExit)
    assert disposed and widget.process is None and widget.active is None
    assert not widget.running and not widget.processing_timer.isActive()
    assert 'Could not save' in widget.message.text()


def test_failed_review_save_keeps_selection_and_live_text(qtbot, tmp_path, result, monkeypatch):
    widget, _ = prepared(qtbot, tmp_path, result)
    first = widget.result_job
    widget.add_sources(['https://example.com/second'], 'url')
    widget.transcript.editItem(widget.transcript.item(0, 3))
    widget.delegate.editor.setPlainText('Unsaved live words')
    with monkeypatch.context() as patch:
        import transcript_desk.review_storage as module
        patch.setattr(module, 'save_checkpoint', lambda *a, **kw: (_ for _ in ()).throw(OSError('Disk full')))
        widget.queue.selectRow(1)
        assert widget.result_job is first and widget.selected() is first
        assert widget.result['segments'][0]['text'] == 'Unsaved live words'
        assert widget.dirty
        assert not widget.close()
    assert widget.save_edits()


def test_history_shares_only_detached_unchanged_subtrees(result):
    first = snapshot(result)
    result['segments'][0]['text'] = 'Changed'
    second = snapshot(result, first)
    assert second['segments'][1] is first['segments'][1]
    assert second['segments'][0] is not first['segments'][0]
    result['segments'][1]['text'] = 'Later change'
    assert second['segments'][1]['text'] != 'Later change'
    assert first['segments'][0]['text'] != 'Changed'


def test_exception_diagnostic_omits_exception_message_and_source_text():
    try:
        raise ValueError('private transcript and hf_secret')
    except ValueError as exc:
        recorded = json.dumps(exception_record(type(exc), exc.__traceback__))
    assert 'ValueError' in recorded and 'test_review_safety.py' in recorded
    assert 'private transcript' not in recorded and 'hf_secret' not in recorded


def test_replacement_retries_brief_windows_sharing_violations(tmp_path, monkeypatch):
    import os
    from transcript_desk import file_replace
    source, target = tmp_path / 'new', tmp_path / 'target'
    source.write_text('new')
    target.write_text('old')
    real, refusals = os.replace, [2]

    def held(*args):
        if refusals[0]:
            refusals[0] -= 1
            raise PermissionError(13, 'The process cannot access the file')
        return real(*args)
    monkeypatch.setattr(file_replace, 'RETRY', True)
    monkeypatch.setattr(file_replace, 'DELAYS', (0, 0, 0))
    monkeypatch.setattr(file_replace.os, 'replace', held)
    file_replace.replace_file(source, target)
    assert target.read_text() == 'new'
    refusals[0] = 4
    source.write_text('newer')
    import pytest
    with pytest.raises(PermissionError):
        file_replace.replace_file(source, target)


def test_default_output_keeps_existing_folder_and_uses_reported_documents(tmp_path, monkeypatch):
    from pathlib import Path
    from PySide6.QtCore import QStandardPaths
    from transcript_desk import runtime
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda location: str(tmp_path / 'OneDrive' / 'Documents'))
    assert runtime.default_output_folder() == tmp_path / 'OneDrive' / 'Documents' / 'Marcadores de Paula'
    (tmp_path / 'home' / 'Documents' / 'Marcadores de Paula').mkdir(parents=True)
    assert runtime.default_output_folder() == tmp_path / 'home' / 'Documents' / 'Marcadores de Paula'
