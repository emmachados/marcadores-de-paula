import copy
import json
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from transcript_desk import worker
from transcript_desk.checkpoints import Checkpoints, can_resume
from transcript_desk.processing_errors import diagnostic_issue_record, issue_from_exception, issue_text, process_exit_issue
from test_worker import make_wav


def fake_engine(monkeypatch, failed_stage):
    from transcript_desk.speaker_assignment import assign_speakers
    monkeypatch.setattr(worker, 'assign_speakers', assign_speakers)
    whisperx = ModuleType('whisperx')
    raw = {'language': 'en', 'segments': [{'start': 0, 'end': 1, 'text': 'Retained words.'}]}
    calls = []

    def operation(stage, result):
        def call(*args, **kwargs):
            calls.append(stage)
            if stage == failed_stage:
                raise RuntimeError(f'Injected {stage} failure')
            return copy.deepcopy(result)
        return call

    whisperx.load_audio = lambda _: [0] * 16000
    whisperx.load_model = operation('speech_model_loading', SimpleNamespace(
        transcribe=operation('transcription', raw)))
    whisperx.load_align_model = operation('alignment', (object(), {}))
    whisperx.align = lambda segments, *a, **kw: {'segments': segments}
    diarize = ModuleType('whisperx.diarize')
    speakers = SimpleNamespace(itertuples=lambda: iter([SimpleNamespace(start=0, end=1, speaker='A')]))
    diarize.DiarizationPipeline = operation('speaker_model_loading', operation('diarization', speakers))
    monkeypatch.setitem(sys.modules, 'whisperx', whisperx)
    monkeypatch.setitem(sys.modules, 'whisperx.diarize', diarize)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace())
    monkeypatch.setattr(worker, 'prepare_torch_libraries', lambda: None)
    monkeypatch.setattr(worker, 'cached_silero_vad', lambda: None)
    monkeypatch.setattr(worker.importlib.metadata, 'version', lambda _: 'fixture')
    from transcript_desk import media_dependencies
    monkeypatch.setattr(media_dependencies, 'prepare_media_libraries', lambda: None)
    if failed_stage == 'speaker_assignment':
        def failed_assignment(intervals, result):
            result['segments'][0]['text'] = 'Mutation must not be published.'
            raise RuntimeError('Injected speaker_assignment failure')
        monkeypatch.setattr(worker, 'assign_speakers', failed_assignment)
    return calls


@pytest.mark.parametrize('stage', ['alignment', 'speaker_model_loading', 'diarization', 'speaker_assignment'])
def test_recoverable_stage_failure_preserves_transcript_exports_and_retry_checkpoint(tmp_path, monkeypatch, stage):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg not installed')
    audio = tmp_path / 'source.wav'
    make_wav(audio)
    calls = fake_engine(monkeypatch, stage)
    events = []
    monkeypatch.setattr(worker, 'emit', lambda kind, **data: events.append({'event': kind, **data}))
    output = tmp_path / 'output'
    config = {'source': str(audio), 'source_type': 'file', 'output': str(output), 'ffmpeg': ffmpeg,
              'transcribe': True, 'diarize': True, 'save_audio': True, 'save_video': False,
              'model': 'large-v3-turbo', 'device': 'cpu', 'speakers': 2, 'formats': ['TXT']}
    worker.run(config)
    result = json.loads((output / 'result.json').read_text())
    assert result['segments'][0]['text'] == 'Retained words.'
    assert result['metadata']['processing_issues'][0]['stage'] == stage
    assert result['metadata']['processing_issues'][0]['category'] == 'unknown'
    assert result['metadata']['processing_issues'][0]['partial'] is True
    assert result['metadata']['processing_stages'][stage]['status'] == 'failed'
    assert (output / 'transcript.txt').is_file()
    assert 'Retained words.' in (output / 'transcript.txt').read_text()
    assert (output / 'asr-original.json').is_file()
    checkpoint = Checkpoints(config)
    assert checkpoint.data['stage'] == ('transcribed' if stage == 'alignment' else 'aligned')
    assert calls.index('transcription') < calls.index('speaker_model_loading')
    warnings = [event for event in events if event['event'] == 'warning']
    assert warnings[0]['issue']['stage'] == stage
    assert events[-1]['event'] == 'complete' and events[-1]['issues']
    assert not any(event['event'] == 'error' for event in events)
    job = {'config': config, 'status': 'complete', 'processing_issues': events[-1]['issues']}
    assert can_resume(job)
    assert not can_resume({**job, 'processing_issues': []})
    original = (output / 'asr-original.json').read_bytes()
    audio.unlink()  # Recovery must use saved media and recognition.
    retry_calls = fake_engine(monkeypatch, None)
    worker.run(config)
    assert 'transcription' not in retry_calls
    assert ('alignment' in retry_calls) == (stage == 'alignment')
    assert 'diarization' in retry_calls
    recovered = json.loads((output / 'result.json').read_text())
    assert recovered['metadata']['diarization'] == 'complete'
    assert not recovered['metadata'].get('processing_issues')
    assert not (output / '.processing').exists()
    assert (output / 'asr-original.json').read_bytes() == original


@pytest.mark.parametrize('stage', ['speech_model_loading', 'transcription'])
def test_fatal_recognition_failure_reports_stage_and_no_transcript(tmp_path, monkeypatch, stage):
    fake_engine(monkeypatch, stage)
    events = []
    monkeypatch.setattr(worker, 'emit', lambda kind, **data: events.append({'event': kind, **data}))
    config = {'output': str(tmp_path)}
    path = tmp_path / 'job.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(worker, 'run', lambda config: worker.transcribe(
        {'device': 'cpu', 'model': 'large-v3-turbo', 'diarize': True, 'speakers': 2},
        tmp_path / 'audio.wav', {'warnings': []}, tmp_path))
    assert worker.main(path) == 1
    assert events[-1]['event'] == 'error'
    assert events[-1]['issue']['stage'] == stage
    assert events[-1]['partial'] is False
    assert not (tmp_path / 'result.json').exists()


def test_model_access_is_stage_specific_and_unknown_errors_stay_unknown(monkeypatch):
    token = 'hf_privatecredential'
    monkeypatch.setenv('HF_TOKEN', token)
    denied = RuntimeError(f'403 request refused: {token}')
    denied.response = SimpleNamespace(status_code=403)
    issue = issue_from_exception(denied, 'speaker_model_loading', partial=True)
    assert issue['category'] == 'model_access'
    assert 'Hugging Face' in issue['action']
    assert token not in issue_text(issue, include_details=True)
    unknown = issue_from_exception(RuntimeError('CUDA and 403 occur in this arbitrary fixture'), 'transcription')
    assert unknown['category'] == 'unknown'
    assert 'cause' not in unknown['summary']
    assert 'arbitrary fixture' in unknown['details']


def test_diagnostic_record_never_includes_local_details_or_arbitrary_enum_values():
    record = diagnostic_issue_record({'stage': 'private transcript', 'category': 'hf_secret',
                                      'details': '/private/recording confidential text', 'partial': True})
    assert record == {'event': 'processing_issue', 'stage': 'unknown', 'category': 'unknown', 'partial': True}


def test_diagnostics_accept_explicit_retained_smoke_folder(tmp_path, monkeypatch):
    import transcript_desk.diagnostics as module
    monkeypatch.setattr(module, '_active', None)
    folders = []
    fake = object()
    monkeypatch.setattr(module, 'Diagnostics', lambda folder: folders.append(folder) or fake)
    folder = tmp_path / 'reports' / 'diagnostics' / 'processing-fault'
    assert module.install_diagnostics(folder=folder) is fake
    assert folders == [folder]


@pytest.mark.parametrize('code,crashed,category', [(0, False, 'unexpected_exit'), (-11, True, 'native_crash'),
                                                 (0xC0000005, True, 'native_crash')])
def test_unexpected_exit_distinguishes_native_crash(code, crashed, category):
    issue = process_exit_issue(code, crashed=crashed, stage='diarization', partial=True)
    assert issue['category'] == category
    assert issue['stage'] == 'diarization'
    assert 'cause is unknown' in issue['details']
    assert issue['partial'] is True
    assert str(code) in issue['details']


def test_unexpected_exit_keeps_last_worker_output():
    output = 'early line\n' * 1000 + 'No Python at "C:\\Users\\p\\python.exe"\nhf_secretvalue\n'
    issue = process_exit_issue(103, stage='worker_start', output=output)
    assert issue['category'] == 'unexpected_exit'
    assert 'No Python at' in issue['details']
    assert 'cause is unknown' not in issue['details']
    assert 'hf_secretvalue' not in issue['details']
    assert len(issue['details']) < 4200


def test_worker_subprocess_emits_structured_validation_failure(tmp_path):
    config = {'source': str(tmp_path / 'missing.wav'), 'source_type': 'file', 'output': str(tmp_path / 'output')}
    path = tmp_path / 'job.json'
    path.write_text(json.dumps(config))
    child = subprocess.run([sys.executable, '-m', 'transcript_desk.worker', str(path)],
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 1
    event = json.loads(child.stdout.splitlines()[-1])
    assert event['issue']['stage'] == 'validation'
    assert event['issue']['category'] == 'invalid_input'


def test_processing_issue_dialog_shows_and_copies_local_redacted_details(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication, QPushButton
    from transcript_desk.processing_issue_dialog import ProcessingIssueDialog
    monkeypatch.setenv('HF_TOKEN', 'hf_privatecredential')
    issue = issue_from_exception(RuntimeError('Actual error hf_privatecredential'), 'alignment', partial=True)
    dialog = ProcessingIssueDialog(issue)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.isVisible()
    assert 'Word alignment failed' in dialog.details.toPlainText()
    copy_button = next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Copy details')
    copy_button.click()
    copied = QApplication.clipboard().text()
    assert 'Actual error [redacted]' in copied
    assert 'hf_privatecredential' not in copied
