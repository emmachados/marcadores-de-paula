import json
from types import SimpleNamespace

from PySide6.QtCore import QProcess

from transcript_desk.processing_errors import issue_from_exception
from transcript_desk.processing_issue_dialog import ProcessingIssueDialog
from test_autosave import prepared


def protocol(window, events):
    disposed = []
    data = ('\n'.join(json.dumps(event) for event in events) + '\n').encode('utf-8')
    window.process = SimpleNamespace(readAllStandardOutput=lambda: data,
                                     readAllStandardError=lambda: b'',
                                     deleteLater=lambda: disposed.append(True))
    window.active = window.result_job
    return disposed


def test_warning_completion_retains_issue_details_and_review(qtbot, tmp_path, result):
    widget, _ = prepared(qtbot, tmp_path, result)
    issue = issue_from_exception(MemoryError('Synthetic allocation failure'), 'diarization', partial=True)
    protocol(widget, [
        {'event': 'progress', 'stage': 'Identifying speakers', 'stage_id': 'diarization'},
        {'event': 'warning', 'message': issue['summary'], 'issue': issue},
        {'event': 'complete', 'title': 'Retained transcript', 'issues': [issue]}])
    widget.read_stdout()
    assert widget.processing_stage_id == 'diarization'
    assert widget.active['status'] == 'complete'
    assert 'Complete with warnings' in widget.active['message']
    assert widget.active['processing_issues'] == [issue]
    assert widget.issue_button.isEnabled()
    dialog = ProcessingIssueDialog(widget.processing_issues(widget.selected())[0], widget)
    qtbot.addWidget(dialog)
    assert 'Synthetic allocation failure' in dialog.details.toPlainText()
    assert 'transcript is retained' in dialog.details.toPlainText()
    widget.active = widget.process = None


def test_native_crash_records_last_stage_and_unsigned_windows_code(qtbot, tmp_path, result):
    widget, _ = prepared(qtbot, tmp_path, result)
    original_text = [segment['text'] for segment in widget.result['segments']]
    disposed = protocol(widget, [])
    widget.active['status'] = 'running'
    widget.processing_stage_id = 'transcription'
    widget.job_finished(-1073741819, QProcess.CrashExit)
    job = widget.result_job
    assert job['status'] == 'failed' and job['partial']
    issue = job['processing_issues'][0]
    assert issue['category'] == 'native_crash' and issue['stage'] == 'transcription'
    assert '0xC0000005'.lower() in issue['details'].lower()
    assert 'cause is unknown' in issue['details']
    assert disposed and widget.process is None
    assert [segment['text'] for segment in widget.result['segments']] == original_text


def test_missing_speaker_token_does_not_block_transcription(qtbot, tmp_path, result, monkeypatch):
    import transcript_desk.window as module
    widget, _ = prepared(qtbot, tmp_path, result)
    widget.token = ''
    widget.result_job.update(status='queued')
    widget.result_job['config'].update(transcribe=True, diarize=True, speakers=0)
    monkeypatch.setattr(module, 'tool_path', lambda _: 'available')
    monkeypatch.setattr(module, 'engine_ready', lambda _: True)
    monkeypatch.setattr(module, 'engine_start_error', lambda _: '')
    started = []
    monkeypatch.setattr(widget, 'next_job', lambda preferred=None: started.append(True))
    widget.start_queue()
    assert started and widget.running
    widget.running = False


def test_malformed_stored_issue_does_not_break_recording_selection(qtbot, tmp_path, result):
    widget, _ = prepared(qtbot, tmp_path, result)
    for malformed in (None, 'bad', [{'title': []}], 42):
        widget.result_job['processing_issues'] = malformed
        widget.update_controls()
        assert widget.processing_issues(widget.result_job) == []
        assert not widget.issue_button.isEnabled()


def test_engine_that_cannot_start_blocks_transcription_with_setup_advice(qtbot, tmp_path, result, monkeypatch):
    import transcript_desk.window as module
    widget, _ = prepared(qtbot, tmp_path, result)
    widget.result_job.update(status='queued')
    widget.result_job['config'].update(transcribe=True)
    monkeypatch.setattr(module, 'tool_path', lambda _: 'available')
    monkeypatch.setattr(module, 'engine_ready', lambda _: True)
    monkeypatch.setattr(module, 'engine_start_error', lambda _: 'error: uv trampoline failed to spawn Python child process')
    started = []
    monkeypatch.setattr(widget, 'next_job', lambda preferred=None: started.append(True))
    widget.start_queue()
    assert not started and not widget.running
    assert 'cannot start' in widget.message.text() and 'trampoline' in widget.message.text()
    assert widget.result_job['status'] == 'queued'
