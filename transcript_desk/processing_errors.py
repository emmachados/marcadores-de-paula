"""Stage-specific processing issues. Detailed text stays in the local project/UI."""
from __future__ import annotations

import os
import re
import traceback
from pathlib import Path

STAGE_LABELS = {
    'validation': 'Recording settings', 'checkpoint': 'Saved processing files',
    'media_acquisition': 'Media download', 'media_inspection': 'Media inspection',
    'media_conversion': 'Audio conversion', 'audio_preparation': 'Transcription audio preparation',
    'engine_loading': 'Processing engine loading', 'audio_loading': 'Transcription audio loading',
    'speech_model_loading': 'Speech model loading', 'transcription': 'Speech transcription',
    'alignment': 'Word alignment', 'speaker_model_loading': 'Speaker model loading',
    'diarization': 'Speaker identification', 'speaker_assignment': 'Speaker assignment',
    'export': 'Transcript export', 'output_saving': 'Result saving',
    'worker_start': 'Processing worker startup', 'unknown': 'Unknown processing stage',
}
SPEAKER_STAGES = {'speaker_model_loading', 'diarization', 'speaker_assignment'}
CATEGORIES = {'unknown', 'model_access', 'memory', 'processor', 'network', 'storage',
              'dependency', 'invalid_input', 'native_crash', 'unexpected_exit', 'worker_start'}


def redact_details(text):
    text = str(text)
    for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HUGGINGFACEHUB_API_TOKEN'):
        token = os.environ.get(name)
        if token:
            text = text.replace(token, '[redacted]')
    text = re.sub(r'\bhf_[A-Za-z0-9]+\b', '[redacted]', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+', r'\1[redacted]', text)
    text = re.sub(r'(?i)([?&](?:token|access_token|api_key|key)=)[^&\s]+', r'\1[redacted]', text)
    text = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', text)
    return text[:12000]


def issue_from_exception(exc, stage=None, partial=False):
    stage = stage or getattr(exc, 'processing_stage', 'unknown')
    if stage not in STAGE_LABELS:
        stage = 'unknown'
    category = 'unknown'
    action = 'Read the local error details. Retry the recording; if the error repeats, export diagnostics for investigation.'
    text = str(exc).casefold()
    response = getattr(exc, 'response', None)
    status_code = getattr(response, 'status_code', None)
    kind = type(exc).__name__
    if stage in {'speech_model_loading', 'alignment', 'speaker_model_loading'} and (
            status_code in (401, 403) or kind in {'GatedRepoError', 'RepositoryNotFoundError', 'HfHubHTTPError'}
            and any(term in text for term in ('401', '403', 'gated', 'unauthorized', 'forbidden'))):
        category = 'model_access'
        action = ('Open Setup, check the Hugging Face read token and accept the speaker model terms, then retry speaker identification.'
                  if stage in SPEAKER_STAGES else
                  'Check access to the speech or alignment model and the network connection, then retry.')
    elif isinstance(exc, MemoryError) or 'out of memory' in text or kind == 'OutOfMemoryError':
        category = 'memory'
        action = 'Close other applications using memory and retry. For NVIDIA processing, select CPU in Setup or use large-v3-turbo.'
    elif any(term in text for term in ('cuda is unavailable', 'nvidia processing is unavailable',
                                     'no cuda gpus are available', 'cuda driver version is insufficient',
                                     'no kernel image is available', 'cuda error:', 'cublas_status', 'cudnn_status')):
        category = 'processor'
        action = 'Check the NVIDIA driver and the selected engine in Setup, or select CPU and install the CPU engine.'
    elif isinstance(exc, (ConnectionError, TimeoutError)) or kind in {
            'ConnectTimeout', 'ReadTimeout', 'ConnectionError', 'ConnectError', 'ReadTimeoutError', 'LocalEntryNotFoundError'}:
        category = 'network'
        action = 'Check the connection and any required proxy. Retry when model files or media downloads are accessible.'
    elif isinstance(exc, OSError) and getattr(exc, 'errno', None) in (13, 28, 30) or isinstance(exc, PermissionError):
        category = 'storage'
        action = 'Check free disk space and write access to the recording and runtime folders, then retry.'
    elif isinstance(exc, (ImportError, ModuleNotFoundError)) or 'dll load failed' in text:
        category = 'dependency'
        action = 'Open Setup and reinstall the selected processing engine. If it repeats, export diagnostics.'
    elif stage in {'validation', 'checkpoint'} and isinstance(exc, ValueError):
        category = 'invalid_input'
        action = 'Correct the recording settings or follow the checkpoint error details. Retry from start creates a separate attempt.'
    label = STAGE_LABELS[stage]
    summary = f'{label} failed.'
    if partial and stage == 'alignment':
        summary += ' The transcript and segment timings are retained; word timings are unavailable.'
    elif partial and stage in SPEAKER_STAGES:
        summary += ' The transcript is retained; automatic speaker identification is incomplete.'
    elif partial:
        summary += ' A partial transcript is available for review.'
    details = f'{kind}: {redact_details(str(exc))}'
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if frames:
        details += '\n\nStack locations:\n' + '\n'.join(
            f'{Path(frame.filename).name}:{frame.lineno} in {frame.name}' for frame in frames)
    return {'stage': stage, 'stage_label': label, 'category': category,
            'title': f'{label} failed', 'summary': summary, 'action': action,
            'details': details, 'partial': bool(partial), 'exception': kind}


def process_exit_issue(code, crashed=False, stage='unknown', partial=False, failed_to_start=False, output=''):
    if stage not in STAGE_LABELS:
        stage = 'unknown'
    category = 'worker_start' if failed_to_start else ('native_crash' if crashed else 'unexpected_exit')
    title = ('Processing worker could not start' if failed_to_start else
             'Processing worker crashed' if crashed else 'Processing worker stopped unexpectedly')
    summary = f'{title}. Last reported stage: {STAGE_LABELS[stage]}.'
    if partial:
        summary += ' A saved transcript is available for review.'
    return {'stage': stage, 'stage_label': STAGE_LABELS[stage], 'category': category,
            'title': title, 'summary': summary,
            'action': ('Open Setup and check or reinstall the selected processing engine.' if failed_to_start else
                       'Resume to reuse saved stages, or retry from start. Export diagnostics if the process stops again.'),
            'details': f'Process exit code: {code} (0x{code & 0xffffffff:08X}).\n' + (
                'No structured worker error was received. Last worker output:\n\n' + redact_details(output.strip()[-4000:])
                if output.strip() else 'No structured worker error was received. The cause is unknown.'),
            'partial': bool(partial), 'exit_code': int(code)}


def issue_text(issue, include_details=False):
    text = '\n\n'.join(str(issue.get(key, '')) for key in ('summary', 'action') if issue.get(key))
    if include_details:
        text += f"\n\nStage: {issue.get('stage', 'unknown')}\nCategory: {issue.get('category', 'unknown')}"
        if issue.get('details'):
            text += '\n\n' + redact_details(issue['details'])
    return text


def diagnostic_issue_record(issue):
    """Write only known stage and category values to support logs."""
    stage, category = issue.get('stage'), issue.get('category')
    record = {'event': 'processing_issue',
              'stage': stage if stage in STAGE_LABELS else 'unknown',
              'category': category if category in CATEGORIES else 'unknown',
              'partial': bool(issue.get('partial'))}
    code = issue.get('exit_code')
    if isinstance(code, int):
        record['exit_code'] = code
    return record
