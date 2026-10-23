import copy
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from transcript_desk.checkpoints import Checkpoints, file_hash
from transcript_desk import file_replace
from transcript_desk import worker
from test_worker import make_wav


def test_resume_reuses_media_transcription_and_alignment_after_stage_interruptions(tmp_path, monkeypatch):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg not installed')
    source = tmp_path / 'speech.wav'
    make_wav(source)
    output = tmp_path / 'recording'
    config = {'source': str(source), 'source_type': 'file', 'output': str(output), 'ffmpeg': ffmpeg,
              'model': 'large-v3-turbo', 'language': 'es', 'device': 'cpu', 'speakers': 2,
              'transcribe': True, 'diarize': True, 'save_video': False, 'save_audio': True,
              'review_before_export': True, 'formats': []}
    calls = {'recognition': 0, 'alignment': 0, 'diarization': 0, 'acquire': 0}
    phase = ['alignment']
    whisperx = ModuleType('whisperx')
    raw = {'language': 'es', 'segments': [{'start': 0, 'end': 1, 'text': 'bueno bueno',
          'words': [{'word': 'bueno', 'start': 0, 'end': .4}, {'word': 'bueno', 'start': .5, 'end': 1}]}]}
    def recognize(*a, **kw):
        calls['recognition'] += 1
        return copy.deepcopy(raw)
    def align(segments, *a, **kw):
        calls['alignment'] += 1
        if phase[0] == 'alignment':
            raise KeyboardInterrupt('Simulated process interruption during alignment')
        return {'segments': segments}
    def diarize(*a, **kw):
        calls['diarization'] += 1
        if phase[0] == 'diarization':
            raise KeyboardInterrupt('Simulated process interruption during diarization')
        return SimpleNamespace(itertuples=lambda: iter([SimpleNamespace(start=0, end=1, speaker='A')]))
    whisperx.load_audio = lambda _: [0] * 16000
    whisperx.load_model = lambda *a, **kw: SimpleNamespace(transcribe=recognize)
    whisperx.load_align_model = lambda **kw: (object(), {})
    whisperx.align = align
    module = ModuleType('whisperx.diarize')
    module.DiarizationPipeline = lambda *a, **kw: diarize
    monkeypatch.setitem(sys.modules, 'whisperx', whisperx)
    monkeypatch.setitem(sys.modules, 'whisperx.diarize', module)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(get_num_threads=lambda: 6, set_num_threads=lambda n: None))
    monkeypatch.setattr(worker, 'prepare_torch_libraries', lambda: None)
    monkeypatch.setattr(worker, 'cached_silero_vad', lambda: None)
    monkeypatch.setattr(worker.importlib.metadata, 'version', lambda _: 'fixture')
    monkeypatch.setattr(worker, 'needs_speaker_model', lambda _: False)
    events = []
    monkeypatch.setattr(worker, 'emit', lambda kind, **values: events.append({'event': kind, **values}))
    acquire = worker.acquire
    def counted_acquire(*a):
        calls['acquire'] += 1
        return acquire(*a)
    monkeypatch.setattr(worker, 'acquire', counted_acquire)
    with pytest.raises(KeyboardInterrupt):
        worker.run(config)
    assert Checkpoints(config).has('transcribed')
    original = (output / 'asr-original.json').read_bytes()
    (output / 'reviewed.json').write_text('Existing review is retained')
    source.unlink()  # Resume must use retained audio, even after moving the input.
    phase[0] = 'diarization'
    with pytest.raises(KeyboardInterrupt):
        worker.run(config)
    assert Checkpoints(config).has('aligned')
    phase[0] = 'complete'
    worker.run(config)
    assert calls == {'recognition': 1, 'alignment': 2, 'diarization': 2, 'acquire': 1}
    assert (output / 'asr-original.json').read_bytes() == original
    assert (output / 'reviewed.json').read_text() == 'Existing review is retained'
    result = json.loads((output / 'result.json').read_text())
    assert result['segments'][0]['text'] == 'bueno bueno'
    assert result['segments'][0]['speaker'] == 'A'
    assert result['metadata']['resume_count'] == 2
    assert events[-1]['event'] == 'complete'
    assert not (output / '.processing').exists()


def test_checkpoint_rejects_changed_settings_audio_and_partial_commit(tmp_path, monkeypatch):
    config = {'output': str(tmp_path), 'source': 'fixture', 'device': 'cpu'}
    checkpoint = Checkpoints(config)
    audio = checkpoint.folder / 'analysis.wav'
    audio.write_bytes(b'original audio')
    metadata = {'analysis_sha256': file_hash(audio)}
    checkpoint.save('prepared', metadata)
    with pytest.raises(ValueError, match='settings differ'):
        Checkpoints({**config, 'device': 'cuda'})
    previous = checkpoint.path.read_bytes()
    def fail(*a):
        raise OSError('Simulated interruption before atomic replacement')
    with monkeypatch.context() as patch:
        patch.setattr(file_replace.os, 'replace', fail)
        with pytest.raises(OSError):
            checkpoint.save('transcribed', metadata, {'segments': []})
    assert checkpoint.path.read_bytes() == previous
    assert Checkpoints(config).data['stage'] == 'prepared'
    audio.write_bytes(b'changed audio')
    with pytest.raises(ValueError, match='missing or changed'):
        Checkpoints(config).validate_audio()
