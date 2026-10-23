import copy
import random

import pytest

from transcript_desk.speaker_assignment import SpeakerIntervals, assign_speakers, review_reasons
from transcript_desk.processing import timed_stage


def test_interval_queries_match_exhaustive_overlap_with_nested_intervals():
    rng = random.Random(17)
    intervals = [(0, 1000, "long")]
    for _ in range(400):
        start = rng.uniform(0, 1000)
        intervals.append((start, start + rng.uniform(0, 12), str(rng.randrange(4))))
    tree = SpeakerIntervals(intervals)
    for _ in range(600):
        start = rng.uniform(-10, 1100)
        end = start + rng.uniform(0, 3)
        expected = {}
        for first, last, speaker in sorted(intervals, key=lambda x: x[0]):
            overlap = min(last, end) - max(first, start)
            if overlap > 0:
                expected[speaker] = expected.get(speaker, 0) + overlap
        assert tree.candidates(start, end) == expected
    assert tree.candidates(1000, 1000) == {}
    assert SpeakerIntervals([]).candidates(0, 1) == {}


def test_assignment_preserves_words_and_marks_ambiguity_without_filling_gaps():
    result = {"segments": [{"start": 0, "end": 5, "text": "Well, well, yes.", "words": [
        {"start": 0, "end": 1, "word": "Well,"},
        {"start": 1, "end": 2, "word": "well,"},
        {"start": 4, "end": 5, "word": "yes."}, {"word": "uh"}]}]}
    original = copy.deepcopy(result)
    assign_speakers([(0, 2, "A"), (1, 2, "B")], result)
    segment = result["segments"][0]
    words = segment["words"]
    assert [w['word'] for w in words] == [w['word'] for w in original['segments'][0]['words']]
    assert segment['text'] == original['segments'][0]['text']
    assert words[0]['speaker'] == words[1]['speaker'] == 'A'
    assert words[1]['speaker_candidates'] == {'A': 1, 'B': 1}
    assert 'speaker' not in words[2] and 'speaker' not in words[3]
    assert len(review_reasons(segment)) == 3
    from transcript_desk.turn_edits import assign_speaker
    assign_speaker(result, 0, 'B')
    assert not review_reasons(segment)
    assert words[1]['speaker_candidates'] == {'A': 1, 'B': 1}


@pytest.mark.parametrize('failed', [False, True])
def test_stage_times_record_completion_and_failure(monkeypatch, failed):
    import transcript_desk.processing as module
    ticks = iter([10.0, 12.5])
    monkeypatch.setattr(module, 'monotonic', lambda: next(ticks))
    metadata, events = {}, []
    def emit(kind, **data):
        events.append({'event': kind, **data})
    try:
        with timed_stage(metadata, emit, 'diarization', 'Identifying speakers'):
            if failed:
                raise ValueError('fixture')
    except ValueError:
        assert failed
    assert metadata['processing_stages']['diarization'] == {'seconds': 2.5, 'status': 'failed' if failed else 'complete'}
    assert events[0]['event'] == 'progress'
    assert events[-1]['event'] == 'timing'


def test_single_speaker_worker_skips_diarizer_and_keeps_missing_word_timings(tmp_path, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    import transcript_desk.worker as worker
    whisperx = ModuleType('whisperx')
    raw = {'language': 'es', 'segments': [{'start': 0, 'end': 1, 'text': 'bueno bueno',
                                         'words': [{'word': 'bueno'}, {'word': 'bueno', 'start': .5, 'end': 1}]}]}
    whisperx.load_audio = lambda _: [0] * 16000
    whisperx.load_model = lambda *a, **kw: SimpleNamespace(transcribe=lambda *a, **kw: copy.deepcopy(raw))
    whisperx.load_align_model = lambda **kw: (object(), {})
    whisperx.align = lambda segments, *a, **kw: {'segments': segments}
    diarize = ModuleType('whisperx.diarize')
    def forbidden(*a, **kw):
        pytest.fail('A declared single-speaker recording must not load the diarizer')
    diarize.DiarizationPipeline = forbidden
    monkeypatch.setitem(sys.modules, 'whisperx', whisperx)
    monkeypatch.setitem(sys.modules, 'whisperx.diarize', diarize)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(get_num_threads=lambda: 6, set_num_threads=lambda n: None))
    monkeypatch.setattr(worker, 'prepare_torch_libraries', lambda: None)
    monkeypatch.setattr(worker, 'cached_silero_vad', lambda: None)
    monkeypatch.setattr(worker.importlib.metadata, 'version', lambda _: 'test')
    monkeypatch.setattr(worker, 'emit', lambda *a, **kw: None)
    from transcript_desk.speaker_assignment import needs_speaker_model
    config = {'device': 'cpu', 'model': 'large-v3-turbo', 'diarize': True, 'speakers': 1}
    assert not needs_speaker_model(config)
    assert needs_speaker_model({**config, 'speakers': 0})
    assert needs_speaker_model({**config, 'speakers': 2})
    result = worker.transcribe(config, tmp_path/'unused.wav', {'warnings': []}, tmp_path)
    assert result['metadata']['diarization'] == 'single_speaker_declared'
    assert result['metadata']['speaker_intervals'] == []
    assert result['segments'][0]['text'] == 'bueno bueno'
    assert all(w['speaker'] == 'SPEAKER_00' for w in result['segments'][0]['words'])
    assert 'start' not in result['segments'][0]['words'][0]
    assert 'diarization' not in result['metadata']['processing_stages']
