"""Processing checkpoints; an interrupted stage starts again."""
import hashlib
import json
import os
from pathlib import Path

from transcript_desk.file_replace import replace_file

STAGES = ("prepared", "transcribed", "aligned", "diarized")
CONFIG_KEYS = ("source", "source_type", "model", "custom_model_path", "language", "device",
               "speakers", "diarize", "transcribe", "save_video", "save_audio")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    replace_file(temporary, path)


class Checkpoints:
    def __init__(self, config):
        self.folder = Path(config['output']) / '.processing'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'checkpoint.json'
        self.settings = {key: config.get(key) for key in CONFIG_KEYS}
        if config.get('model') == 'custom':
            model = Path(config.get('custom_model_path') or '') / 'model.bin'
            self.settings['model_sha256'] = file_hash(model)
        self.data = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
                if self.data.get('schema') != 1 or self.data.get('settings') != self.settings:
                    raise ValueError('The recording settings differ from the saved checkpoint.')
                if self.data.get('stage') not in STAGES:
                    raise ValueError('The saved processing stage is unknown.')
                if not isinstance(self.data.get('metadata'), dict):
                    raise ValueError('The checkpoint metadata are unreadable.')
                if self.data['stage'] != 'prepared' and not isinstance(self.data.get('result'), dict):
                    raise ValueError('The checkpoint transcript is unreadable.')
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f'Cannot resume this checkpoint: {exc} Use Retry from start to create a separate attempt.') from exc

    def has(self, stage):
        return bool(self.data) and STAGES.index(self.data['stage']) >= STAGES.index(stage)

    def save(self, stage, metadata, result=None):
        data = {'schema': 1, 'settings': self.settings, 'stage': stage,
                'metadata': metadata, 'result': result}
        write_json(self.path, data)
        self.data = data

    def validate_audio(self):
        path = self.folder / 'analysis.wav'
        expected = self.data['metadata'].get('analysis_sha256')
        if not path.is_file() or not expected or file_hash(path) != expected:
            raise ValueError('The saved processing audio is missing or changed. Use Retry from start to create a separate attempt.')
        return path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def can_resume(job):
    recoverable_completion = job.get('status') == 'complete' and bool(job.get('processing_issues'))
    return (job.get('status') in ('paused', 'interrupted', 'failed') or recoverable_completion) and (
        job.get('status') == 'paused' or
        (Path(job['config']['output']) / '.processing/checkpoint.json').is_file())
