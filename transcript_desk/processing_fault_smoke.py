"""Shipped GUI and worker checks using synthetic engine failures, without models."""
import copy
import json
import sys
import traceback
import wave
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton


def run_processing_fault_check(application, window, report_path):
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    status = {'synthetic_fixture': True, 'checks': []}

    def check():
        try:
            from transcript_desk import worker
            from transcript_desk.checkpoints import Checkpoints, can_resume
            from transcript_desk.runtime import data_root, tool_path
            from transcript_desk.processing_issue_dialog import ProcessingIssueDialog
            from transcript_desk.processing_errors import diagnostic_issue_record, process_exit_issue
            from transcript_desk.speaker_assignment import assign_speakers
            root = data_root() / 'Synthetic processing faults'
            root.mkdir(parents=True, exist_ok=True)
            audio = root / 'synthetic.wav'
            with wave.open(str(audio), 'wb') as stream:
                stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0\0' * 16000)
            failed = [None]
            calls = []
            raw = {'language': 'en', 'segments': [{'start': 0, 'end': 1, 'text': 'Synthetic retained words.'}]}

            def operation(stage, value):
                def invoke(*args, **kwargs):
                    calls.append(stage)
                    if failed[0] == stage:
                        if stage == 'speaker_model_loading':
                            error = RuntimeError('Synthetic speaker model HTTP 403 refusal')
                            error.response = SimpleNamespace(status_code=403)
                            raise error
                        raise RuntimeError(f'Synthetic {stage} failure')
                    return copy.deepcopy(value)
                return invoke

            whisperx = ModuleType('whisperx')
            whisperx.load_audio = lambda _: [0] * 16000
            whisperx.load_model = operation('speech_model_loading', SimpleNamespace(transcribe=operation('transcription', raw)))
            whisperx.load_align_model = operation('alignment', (object(), {}))
            whisperx.align = lambda segments, *args, **kwargs: {'segments': segments}
            speakers = SimpleNamespace(itertuples=lambda: iter([SimpleNamespace(start=0, end=1, speaker='A')]))
            diarize = ModuleType('whisperx.diarize')
            diarize.DiarizationPipeline = operation('speaker_model_loading', operation('diarization', speakers))

            def assignment(intervals, result):
                if failed[0] == 'speaker_assignment':
                    result['segments'][0]['text'] = 'Synthetic mutation that must be discarded.'
                    raise RuntimeError('Synthetic speaker assignment failure')
                return assign_speakers(intervals, result)

            events = []
            with ExitStack() as patches:
                patches.enter_context(patch.dict(sys.modules, {'whisperx': whisperx, 'whisperx.diarize': diarize,
                                                                'torch': SimpleNamespace()}))
                patches.enter_context(patch.object(worker, 'prepare_torch_libraries', lambda: None))
                patches.enter_context(patch.object(worker, 'cached_silero_vad', lambda: None))
                patches.enter_context(patch('transcript_desk.media_dependencies.prepare_media_libraries', lambda: None))
                patches.enter_context(patch.object(worker.importlib.metadata, 'version', lambda _: 'synthetic-smoke'))
                patches.enter_context(patch.object(worker, 'assign_speakers', assignment))
                patches.enter_context(patch.object(worker, 'emit', lambda kind, **data: events.append({'event': kind, **data})))
                for stage in ('alignment', 'speaker_model_loading', 'diarization', 'speaker_assignment'):
                    events.clear()
                    calls.clear()
                    failed[0] = stage
                    folder = root / stage
                    config = {'source': str(audio), 'source_type': 'file', 'output': str(folder), 'ffmpeg': tool_path('ffmpeg'),
                              'transcribe': True, 'diarize': True, 'save_audio': True, 'save_video': False,
                              'model': 'large-v3-turbo', 'device': 'cpu', 'speakers': 2, 'formats': ['TXT']}
                    worker.run(config)
                    saved = json.loads((folder / 'result.json').read_text())
                    assert saved['segments'][0]['text'] == 'Synthetic retained words.'
                    assert (folder / 'transcript.txt').exists()
                    assert events[-1]['event'] == 'complete'
                    issue = events[-1]['issues'][0]
                    assert issue['stage'] == stage and issue['partial']
                    assert issue['category'] == ('model_access' if stage == 'speaker_model_loading' else 'unknown')
                    job = {'id': stage, 'source': str(audio), 'title': stage, 'config': config, 'status': 'running'}
                    window.store.jobs.append(job)
                    window.active = job
                    window.buffer = b''
                    payload = '\n'.join(json.dumps(event) for event in events).encode() + b'\n'
                    class EventPipe:
                        def readAllStandardOutput(self):
                            return payload
                    window.process = EventPipe()
                    window.read_stdout()
                    window.process = window.active = None
                    assert job['status'] == 'complete'
                    assert job['processing_issues'][0]['stage'] == stage
                    assert 'warnings' in job['message'].casefold()
                    assert can_resume(job)
                    window.refresh_queue()
                    window.queue.selectRow(len(window.store.jobs) - 1)
                    window.update_controls()
                    assert window.issue_button.isEnabled()
                    dialog = ProcessingIssueDialog(issue, window)
                    dialog.show()
                    application.processEvents()
                    assert dialog.isVisible()
                    button = next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Copy details')
                    button.click()
                    assert stage in QApplication.clipboard().text()
                    dialog.close()
                    calls.clear()
                    failed[0] = None
                    worker.run(config)
                    assert 'transcription' not in calls
                    assert ('alignment' in calls) == (stage == 'alignment')
                    assert not (folder / '.processing').exists()
                    status['checks'].append(stage + ': transcript, export, warning UI, details copy and checkpoint resume')
                for stage in ('speech_model_loading', 'transcription'):
                    failed[0] = stage
                    events.clear()
                    config['output'] = str(root / stage)
                    config_path = root / (stage + '.json')
                    config_path.write_text(json.dumps(config))
                    assert worker.main(config_path) == 1
                    issue = events[-1]['issue']
                    assert issue['stage'] == stage and not issue['partial']
                    status['checks'].append(stage + ': fatal structured failure')
            issue = process_exit_issue(0xC0000005, crashed=True, stage='diarization', partial=True)
            assert issue['category'] == 'native_crash' and 'cause is unknown' in issue['details']
            assert 'details' not in diagnostic_issue_record(issue)
            status['checks'].append('Native crash classification and diagnostics detail exclusion')
            status.update(ok=True)
            report.write_text(json.dumps(status, indent=2), encoding='utf-8')
            application.exit(0)
        except Exception as exc:
            window.process = window.active = None
            status.update(ok=False, error=str(exc), traceback=traceback.format_exc())
            report.write_text(json.dumps(status, indent=2), encoding='utf-8')
            application.exit(1)

    QTimer.singleShot(100, check)
