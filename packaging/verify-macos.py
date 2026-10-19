"""Exercise the relocated native app with no development tools on PATH."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transcript_desk import __version__


def verify(setup=False, install_location=None, release_dir=None, model_manifest=None):
    model = json.loads(model_manifest.read_text()) if model_manifest else None
    edition = model['edition'] if model else 'stock'
    expect = ['--expect-model-pack', model_manifest] if model else ['--expect-stock']
    release = release_dir or ROOT / 'release' / f'macos-{__version__}-arm64-{edition}'
    source = release / 'Marcadores de Paula.app'
    installed = install_location or ROOT / 'build' / f'Mac installed verification {edition}' / source.name
    installed.parent.mkdir(parents=True, exist_ok=True)
    if installed.exists():
        shutil.rmtree(installed)
    shutil.copytree(source, installed, symlinks=True)
    report = release / 'verification'
    report.mkdir(exist_ok=True)
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'QT_QPA_PLATFORM', 'QT_PLUGIN_PATH',
                'QT_QPA_PLATFORM_PLUGIN_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
        env.pop(key, None)
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    env['TRANSCRIPT_DESK_DATA'] = str(ROOT / 'build' / 'macos-verification-state' / uuid.uuid4().hex)
    binary = installed / 'Contents/MacOS/Marcadores de Paula'
    framework = installed / 'Contents/Frameworks'
    results = {}

    def check(name, command, timeout=120):
        started = time.monotonic()
        completed = subprocess.run([str(part) for part in command], env=env, capture_output=True,
                                   text=True, timeout=timeout)
        (report / f'{name}.log').write_text(completed.stdout + completed.stderr)
        results[name] = {'exit_code': completed.returncode, 'seconds': round(time.monotonic() - started, 2)}
        (report / 'checks.json').write_text(json.dumps(results, indent=2) + '\n')
        if completed.returncode:
            raise RuntimeError(f'{name} failed ({completed.returncode}): {completed.stdout[-2000:]}{completed.stderr[-2000:]}')

    check('signature', ['codesign', '--verify', '--deep', '--strict', installed])
    if any('virtualkeyboard' in path.name.lower() for path in installed.rglob('*')):
        raise RuntimeError('The unused Virtual Keyboard component must not be bundled.')
    cocoa_manifest = json.loads((installed / 'Contents/Resources/licenses/Qt-Cocoa-accessibility/manifest.json').read_text())
    cocoa_plugin = framework / 'PySide6/Qt/plugins/platforms/libqcocoa.dylib'
    if hashlib.sha256(cocoa_plugin.read_bytes()).hexdigest() != cocoa_manifest['plugin_sha256']:
        raise RuntimeError('The installed Cocoa plugin differs from the recorded accessibility repair.')
    (report / 'cocoa-repair.json').write_text(json.dumps(cocoa_manifest, indent=2) + '\n')
    audit = []
    magic = {b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
    for item in installed.rglob('*'):
        if item.is_symlink() or not item.is_file():
            continue
        with item.open('rb') as stream:
            if stream.read(4) not in magic:
                continue
        links = subprocess.check_output(['otool', '-L', str(item)], text=True)
        external = [line.strip().split(' (', 1)[0] for line in links.splitlines()[1:]
                    if line.strip().startswith('/') and not line.strip().startswith(('/System/Library/', '/usr/lib/'))]
        if external:
            raise RuntimeError(f'Unbundled libraries in {item}: {external}')
        load = subprocess.check_output(['otool', '-l', str(item)], text=True)
        versions = re.findall(r'\bminos ([0-9.]+)', load)
        if any(tuple(map(int, version.split('.'))) > (26, 0, 0) for version in versions):
            raise RuntimeError(f'{item} requires macOS later than the declared 26.0: {versions}')
        audit.append({'path': str(item.relative_to(installed)), 'minimum_macos': versions})
    (report / 'binary-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    for tool in ('ffmpeg', 'ffprobe', 'uv', 'deno'):
        check(tool, [framework / 'tools' / tool, '-version' if tool in ('ffmpeg', 'ffprobe') else '--version'])
    with tempfile.TemporaryDirectory(prefix='paula-mac-media-') as temporary:
        work = Path(temporary)
        audio = work / 'test audio.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * 16000)
        job = {'source': str(audio), 'source_type': 'file', 'model': 'large-v3-turbo', 'device': 'cpu',
               'output': str(work / 'output'), 'ffmpeg': str(framework / 'tools/ffmpeg'),
               'save_audio': True, 'save_video': False, 'transcribe': False, 'diarize': False, 'formats': []}
        config = work / 'job.json'
        config.write_text(json.dumps(job))
        check('worker', [framework / 'worker/TranscriptWorker', config])
        if not (work / 'output/audio.flac').read_bytes().startswith(b'fLaC'):
            raise RuntimeError('The frozen worker did not produce playable FLAC.')
    check('playback', [binary, '--smoke-test', *expect, '--smoke-report', report / 'default-model.json'])
    check('setup-save', [binary, '--setup-save-smoke-test', '--smoke-report', report / 'setup-save.json'])
    check('review', [binary, '--review-smoke-test', '--smoke-report', report / 'review.json'])
    for name in ('workspace', 'editor', 'speaker', 'processing-fault', 'preservation'):
        check(name, [binary, f'--{name}-smoke-test', '--smoke-report', report / f'{name}.json'])
        result = json.loads((report / f'{name}.json').read_text(encoding='utf-8'))
        if not isinstance(result, dict) or result.get('ok') is not True or 'error' in result:
            raise RuntimeError(f'{name} did not produce a successful verification report without errors.')
    if setup:
        check('setup-install', [binary, '--setup-smoke-test', '--smoke-report', report / 'engine-setup.json'], timeout=1250)
    check('signature-after-workflows', ['codesign', '--verify', '--deep', '--strict', installed])
    (report / 'environment.json').write_text(json.dumps({'installed_app': str(installed), 'PATH': env['PATH'],
        'runtime': env['TRANSCRIPT_DESK_DATA'], 'setup_exercised': setup}, indent=2) + '\n')
    print(json.dumps(results, indent=2))
    return installed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-manifest', type=Path, help='Expect the bundled model pack this manifest describes.')
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--install-location', type=Path)
    parser.add_argument('--release-dir', type=Path)
    args = parser.parse_args()
    verify(args.setup, args.install_location, args.release_dir.resolve() if args.release_dir else None,
           args.model_manifest.resolve() if args.model_manifest else None)
