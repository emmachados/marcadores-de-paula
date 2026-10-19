"""Compare assignment time and labels against the installed WhisperX implementation.

This synthetic benchmark measures assignment only, not neural diarization or
speaker accuracy. No audio, model downloads or credentials are required.
"""
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcript_desk.speaker_assignment import assign_speakers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import pandas as pd
    from whisperx.diarize import assign_word_speakers

    intervals = [(float(i * 3), float(i * 3 + 3.2), f'SPEAKER_{i % 4:02}') for i in range(3600)]
    result = {'segments': []}
    for i in range(3600):
        start = float(i * 3)
        words = [{'word': 'word', 'start': start + j * .3, 'end': start + j * .3 + .25} for j in range(10)]
        result['segments'].append({'start': start, 'end': start + 3, 'text': ' '.join(['word'] * 10), 'words': words})
    frame = pd.DataFrame(intervals, columns=['start', 'end', 'speaker'])
    timings = {'whisperx': [], 'app': []}
    outputs = {}
    for _ in range(3):
        for name, operation in [('whisperx', lambda r: assign_word_speakers(frame, r, fill_nearest=False)),
                                ('app', lambda r: assign_speakers(intervals, r))]:
            trial = copy.deepcopy(result)
            start = perf_counter()
            outputs[name] = operation(trial)
            timings[name].append(perf_counter() - start)
    def labels(output):
        return [(s.get('speaker'), [w.get('speaker') for w in s['words']]) for s in output['segments']]
    assert labels(outputs['whisperx']) == labels(outputs['app']), 'Speaker assignments differ'
    median = {name: statistics.median(values) for name, values in timings.items()}
    report = {'kind': 'synthetic assignment benchmark; not end-to-end or accuracy evaluation',
              'whisperx_version': importlib.metadata.version('whisperx'),
              'intervals': len(intervals), 'words': 36000, 'timeline_seconds': 10800,
              'matching_labels': True, 'seconds': timings, 'median_seconds': median,
              'assignment_speed_ratio': median['whisperx'] / median['app']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
