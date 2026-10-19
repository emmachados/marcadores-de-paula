"""Transcribe public fixtures with installed Mac app code and a verified engine.

The default mode prepares jobs without launching recognition. Pass --run after
the packaged Setup dialog has completed. Existing public model caches are reused
in offline mode; no credential is printed or written to the job configuration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORMATS = ['TXT', 'JSON', 'CSV', 'SRT', 'VTT', 'TextGrid', 'EAF', 'DOCX']


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cache_inventory(hf_cache, torch_cache):
    def present(repository, filename):
        directory = hf_cache / ('models--' + repository.replace('/', '--'))
        return any(path.is_file() for path in directory.glob('snapshots/*/' + filename))
    return {
        'stock_recognition': present('mobiuslabsgmbh/faster-whisper-large-v3-turbo', 'model.bin'),
        'speaker_config': present('pyannote/speaker-diarization-community-1', 'config.yaml'),
        'speaker_segmentation': present('pyannote/speaker-diarization-community-1', 'segmentation/pytorch_model.bin'),
        'speaker_embedding': present('pyannote/speaker-diarization-community-1', 'embedding/pytorch_model.bin'),
        'english_alignment': (torch_cache / 'hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth').is_file(),
        'spanish_alignment': (torch_cache / 'hub/checkpoints/wav2vec2_voxpopuli_base_10k_asr_es.pt').is_file(),
        'silero': (torch_cache / 'hub/snakers4_silero-vad_master').is_dir(),
    }


def validate_outputs(output, diarize):
    result = json.loads((output / 'result.json').read_text())
    segments = result['segments']
    if not segments or not any(segment.get('text', '').strip() for segment in segments):
        raise ValueError('Recognition produced no transcript.')
    if result['metadata'].get('alignment') != 'complete':
        raise ValueError('Word alignment did not complete: ' + repr(result['metadata'].get('warnings')))
    words = result.get('word_segments') or [word for segment in segments for word in segment.get('words', [])]
    aligned = [word for word in words if 'start' in word and 'end' in word]
    if not aligned or any(word['end'] < word['start'] for word in aligned):
        raise ValueError('Recognition has no valid aligned words.')
    if diarize and result['metadata'].get('diarization') != 'complete':
        raise ValueError('Speaker diarization did not complete.')
    exports = {}
    for fmt in FORMATS:
        suffix = 'TextGrid' if fmt == 'TextGrid' else fmt.lower()
        path = output / ('transcript.' + suffix)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f'Missing or empty export: {fmt}')
        exports[fmt] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    exported = json.loads((output / 'transcript.json').read_text())
    if len(exported['segments']) != len(segments):
        raise ValueError('JSON export lost transcript segments.')
    with (output / 'transcript.csv').open(encoding='utf-8-sig', newline='') as stream:
        if len(list(csv.reader(stream))) != len(segments) + 1:
            raise ValueError('CSV export has the wrong number of rows.')
    if ET.parse(output / 'transcript.eaf').getroot().tag != 'ANNOTATION_DOCUMENT':
        raise ValueError('EAF export is not an annotation document.')
    if not (output / 'transcript.vtt').read_text().startswith('WEBVTT'):
        raise ValueError('VTT header is missing.')
    if 'TextGrid' not in (output / 'transcript.TextGrid').read_text()[:100]:
        raise ValueError('TextGrid header is missing.')
    with zipfile.ZipFile(output / 'transcript.docx') as archive:
        if archive.testzip() is not None:
            raise ValueError('DOCX archive is damaged.')
        ET.fromstring(archive.read('word/document.xml'))
    return {'segments': len(segments), 'aligned_words': len(aligned),
            'speakers': sorted({segment.get('speaker', '') for segment in segments}),
            'alignment': result['metadata']['alignment'], 'diarization': result['metadata']['diarization'],
            'recognized_text': ' '.join(segment['text'].strip() for segment in segments),
            'warnings': result['metadata'].get('warnings', []), 'exports': exports,
            'versions': result['metadata'].get('versions', {}),
            'timings': result['metadata'].get('timings', [])}


def verify(args):
    app = args.app.resolve()
    frameworks = app / 'Contents/Frameworks'
    ffmpeg = frameworks / 'tools/ffmpeg'
    engine = args.runtime.resolve() / 'engine-cpu/bin/python'
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cache = cache_inventory(args.hf_cache, args.torch_cache)
    speaker_ready = all(cache[key] for key in ('speaker_config', 'speaker_segmentation', 'speaker_embedding'))
    diarize = args.diarization == 'required' or (args.diarization == 'auto' and speaker_ready)
    model = json.loads(args.model_manifest.read_text()) if args.model_manifest else None
    edition = model['edition'] if model else 'stock'
    fixture = args.fixture or ROOT / 'build/verification/jfk.flac'
    audio = destination / 'fixture.wav'
    subprocess.run([str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(fixture),
                    '-t', '12', '-ar', '16000', '-ac', '1', str(audio)], check=True)
    config = {'source': str(audio), 'source_type': 'file', 'device': 'cpu',
              'model': 'custom' if model else 'large-v3-turbo',
              'language': model['language'] if model else 'en',
              'output': str(destination / 'result'), 'ffmpeg': str(ffmpeg),
              'deno': str(frameworks / 'tools/deno'), 'transcribe': True,
              'save_audio': True, 'save_video': False, 'diarize': diarize, 'speakers': 0,
              'review_before_export': False, 'formats': FORMATS}
    if model:
        config['custom_model_path'] = str(frameworks / 'models' / model['directory'])
    config_path = destination / 'job.json'
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    report = {'edition': edition, 'application': str(app), 'engine': str(engine),
              'fixture': str(fixture), 'fixture_sha256': digest(audio), 'trim_seconds': 12,
              'cache_inventory': cache, 'offline': not args.allow_network,
              'diarization_requested': diarize, 'status': 'prepared',
              'bundled_worker_sha256': digest(frameworks / 'transcript_desk/worker.py')}
    report_path = destination / 'transcription-verification.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    if not args.run:
        print(json.dumps(report, indent=2))
        return
    marker = args.runtime / 'engine-cpu/ready.json'
    if not engine.is_file() or not marker.is_file():
        raise ValueError('Complete the packaged engine Setup before running transcription verification.')
    if json.loads(marker.read_text()).get('requirements_sha256') != digest(frameworks / 'requirements-engine-macos-arm64.txt'):
        raise ValueError('The installed engine does not match the packaged Mac dependency lock.')
    if (destination / 'result').exists():
        raise ValueError('Choose a new output directory so prior checkpoints cannot replace a fresh recognition run.')
    env = os.environ.copy()
    for name in ('HF_TOKEN', 'PYTHONHOME', 'VIRTUAL_ENV', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'DYLD_LIBRARY_PATH'):
        env.pop(name, None)
    env.update(PATH=str(frameworks / 'tools') + ':/usr/bin:/bin:/usr/sbin:/sbin',
               PYTHONPATH=str(frameworks), PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               PYTHONIOENCODING='utf-8', HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0',
               HF_HOME=str(args.hf_cache.parent), HF_HUB_CACHE=str(args.hf_cache),
               TORCH_HOME=str(args.torch_cache), TRANSCRIPT_DESK_FFMPEG_LIB=str(frameworks / 'tools/lib'))
    if not args.allow_network:
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    started = time.monotonic()
    try:
        with (destination / 'worker-events.jsonl').open('w') as stdout, (destination / 'worker.log').open('w') as stderr:
            completed = subprocess.run([str(engine), '-m', 'transcript_desk.worker', str(config_path)],
                                       cwd=str(destination), env=env, stdout=stdout, stderr=stderr, timeout=args.timeout)
        report['exit_code'] = completed.returncode
        signature = subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)],
                                   capture_output=True, text=True)
        report['post_transcription_signature'] = {
            'exit_code': signature.returncode, 'output': signature.stdout + signature.stderr}
        if signature.returncode:
            raise RuntimeError('The application signature is invalid after transcription.')
        if completed.returncode:
            raise RuntimeError('The processing worker failed; see the local worker.log and worker-events.jsonl.')
        report.update(validate_outputs(destination / 'result', diarize), status='passed')
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        report['seconds'] = round(time.monotonic() - started, 2)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-manifest', type=Path, help='Transcribe with the bundled model pack this manifest describes.')
    parser.add_argument('--fixture', type=Path, help='Recording to transcribe; defaults to the stock English fixture.')
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hf-cache', type=Path, default=Path.home() / '.cache/huggingface/hub')
    parser.add_argument('--torch-cache', type=Path, default=Path.home() / '.cache/torch')
    parser.add_argument('--diarization', choices=('auto', 'required', 'disabled'), default='auto')
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--timeout', type=int, default=900)
    verify(parser.parse_args())
