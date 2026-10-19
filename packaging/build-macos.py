"""Build relocatable Apple silicon .app and .dmg releases from prepared tools."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transcript_desk import __version__


def run(*command, **kwargs):
    print(' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def remove_unused_virtual_keyboard(app):
    """Exclude the unused GPL-only input plugin collected by Qt's GUI hook."""
    candidates = sorted((path for path in app.rglob('*')
                         if 'virtualkeyboard' in path.name.lower()),
                        key=lambda path: len(path.parts))
    removed = []
    for path in candidates:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            continue
        removed.append(str(path.relative_to(app)))
    return removed


def build(tools, make_dmg=True, release_dir=None, work_dir=None, cocoa_bundle=None,
          model_manifest=None, model_archive=None):
    model = json.loads(model_manifest.read_text()) if model_manifest else None
    edition = model['edition'] if model else 'stock'
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Build this release natively on Apple silicon macOS.')
    os.chdir(ROOT)
    import importlib.metadata
    pyside_version = importlib.metadata.version('PySide6-Essentials')
    if pyside_version != '6.12.0':
        raise RuntimeError('Mac releases require the validated PySide6 6.12.0 runtime.')
    cocoa_bundle = cocoa_bundle or ROOT / 'build/qt6120-cocoa-package'
    cocoa_manifest = json.loads((cocoa_bundle / 'manifest.json').read_text())
    expected_cocoa = json.loads((ROOT / 'packaging/qt-cocoa/manifest.json').read_text())
    if any(cocoa_manifest.get(key) != value for key, value in expected_cocoa.items()):
        raise RuntimeError('The Cocoa repair does not match the pinned source, SDK and patch recipe.')
    if cocoa_manifest['qt_version'] != pyside_version:
        raise RuntimeError('The patched Cocoa plugin does not match the desktop Qt version.')
    if hashlib.sha256((cocoa_bundle / 'libqcocoa.dylib').read_bytes()).hexdigest() != cocoa_manifest['plugin_sha256']:
        raise RuntimeError('The patched Cocoa plugin failed its SHA-256 check.')
    release = release_dir or ROOT / 'release' / f'macos-{__version__}-arm64-{edition}'
    release.mkdir(parents=True, exist_ok=True)
    work = work_dir or ROOT / 'build' / f'macos-{edition}'
    work.mkdir(parents=True, exist_ok=True)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(work / 'cache')
    for name in ('ffmpeg', 'ffprobe', 'uv', 'deno', 'lib/libavutil.59.dylib'):
        if not (tools / name).is_file():
            raise RuntimeError(f'Missing prepared tool: {name}. Run packaging/prepare-macos-tools.sh.')
    source = work / 'source'
    shutil.copytree(ROOT / 'transcript_desk', source / 'transcript_desk', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    common = ['--noconfirm', '--clean', '--target-architecture', 'arm64',
              '--distpath', str(work / 'dist'), '--workpath', str(work / 'pyinstaller'),
              '--specpath', str(work), '--exclude-module', 'torch', '--exclude-module', 'whisperx',
              '--exclude-module', 'numpy', '--exclude-module', 'scipy', '--exclude-module', 'torchaudio',
              '--exclude-module', 'torchvision', '--exclude-module', 'av',
              '--exclude-module', 'pandas', '--exclude-module', 'matplotlib', '--exclude-module', 'numba',
              '--exclude-module', 'pytest']
    run(sys.executable, '-m', 'PyInstaller', *common, '--onefile', '--console', '--name', 'TranscriptWorker',
        '--collect-all', 'yt_dlp', '--collect-all', 'yt_dlp_ejs', '--exclude-module', 'PySide6', 'worker_entry.py')
    run(sys.executable, '-m', 'PyInstaller', *common, '--onedir', '--windowed', '--name', 'Marcadores de Paula',
        '--osx-bundle-identifier', 'org.transcriptdesk.marcadoresdepaula',
        '--collect-submodules', 'keyring.backends',
        '--add-data', str(ROOT / 'requirements-engine.txt') + ':.',
        '--add-data', str(ROOT / 'requirements-engine-macos-arm64.txt') + ':.',
        '--add-data', str(source / 'transcript_desk') + ':transcript_desk', 'run_app.py')
    app = release / 'Marcadores de Paula.app'
    if app.exists():
        # Only remove this generated edition's app when replacing a build.
        shutil.rmtree(app)
    shutil.copytree(work / 'dist/Marcadores de Paula.app', app, symlinks=True)
    excluded_components = remove_unused_virtual_keyboard(app)
    contents = app / 'Contents'
    frameworks, resources = contents / 'Frameworks', contents / 'Resources'
    shutil.copy2(cocoa_bundle / 'libqcocoa.dylib',
                 frameworks / 'PySide6/Qt/plugins/platforms/libqcocoa.dylib')
    shutil.copytree(tools, frameworks / 'tools', symlinks=True)
    (frameworks / 'tools/manifest.json').replace(resources / 'tools-manifest.json')
    (frameworks / 'worker').mkdir()
    shutil.copy2(work / 'dist/TranscriptWorker', frameworks / 'worker/TranscriptWorker')
    (resources / 'tools').symlink_to('../Frameworks/tools')
    (resources / 'worker').symlink_to('../Frameworks/worker')
    for name in ('README.md', 'THIRD_PARTY.md'):
        shutil.copy2(ROOT / name, resources / name)
    python_manifest = json.loads((ROOT / 'packaging/macos-python.json').read_text())
    python_archive = ROOT / 'build/macos-sources' / f"python-{python_manifest['version']}.tar.gz"
    if hashlib.sha256(python_archive.read_bytes()).hexdigest() != python_manifest['sha256']:
        raise RuntimeError('The bundled Python archive failed its SHA-256 check.')
    python_mirror = resources / 'python-install'
    python_release = python_mirror / python_manifest['release']
    python_release.mkdir(parents=True)
    shutil.copy2(python_archive, python_release / python_manifest['filename'])
    shutil.copy2(ROOT / 'packaging/macos-python.json', python_mirror / 'manifest.json')
    (frameworks / 'python-install').symlink_to('../Resources/python-install')
    shutil.copytree(ROOT / 'docs', resources / 'docs')
    licenses = resources / 'licenses'
    licenses.mkdir()
    shutil.copytree(cocoa_bundle, licenses / 'Qt-Cocoa-accessibility',
                    ignore=shutil.ignore_patterns('libqcocoa.dylib'))
    for name in ('COPYING.LGPLv2.1', 'LICENSE.md'):
        shutil.copy2(ROOT / 'build/macos-sources/ffmpeg-7.1.3' / name, licenses / ('FFmpeg-' + name))
    # Include the matching source and exact build recipe for the bundled LGPL libraries.
    shutil.copy2(ROOT / 'build/macos-sources/ffmpeg-7.1.3.tar.xz', licenses)
    shutil.copy2(ROOT / 'packaging/prepare-macos-tools.sh', licenses)
    shutil.copy2(ROOT / 'build/macos-sources/Deno-LICENSE.md', licenses)
    for file in (ROOT / 'licenses').iterdir():
        shutil.copy2(file, licenses)
    shutil.copy2(ROOT / 'LICENSE', resources / 'LICENSE.txt')
    for distribution in importlib.metadata.distributions():
        for file in distribution.files or []:
            if '.dist-info/licenses/' in str(file):
                target = licenses / distribution.metadata['Name'] / Path(file).name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(distribution.locate_file(file), target)
    if not model:
        (resources / 'stock-default').write_text('large-v3-turbo\n')
        (frameworks / 'stock-default').symlink_to('../Resources/stock-default')
    else:
        archive = model_archive or ROOT / 'release' / model['archive']
        run(sys.executable, 'scripts/verify_release_model.py', archive, model_manifest)
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(resources / 'models')
        (frameworks / 'models').symlink_to('../Resources/models')
    plist = contents / 'Info.plist'
    metadata = plistlib.loads(plist.read_bytes())
    metadata.update(CFBundleDisplayName='Marcadores de Paula', CFBundleShortVersionString=__version__,
                    CFBundleVersion=__version__, LSMinimumSystemVersion='26.0', NSHighResolutionCapable=True)
    plist.write_bytes(plistlib.dumps(metadata))
    run('codesign', '--force', '--deep', '--sign', '-', app)
    run('codesign', '--verify', '--deep', '--strict', app)
    report = {'version': __version__, 'edition': edition, 'architecture': 'arm64',
              'build_macos': platform.mac_ver()[0], 'minimum_macos': '26.0',
              'pyside_version': pyside_version, 'cocoa_accessibility': cocoa_manifest,
              'signature': 'ad-hoc', 'notarized': False,
              'excluded_unused_components': excluded_components,
              'managed_python': python_manifest,
              'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((ROOT / 'transcript_desk').rglob('*.py'))},
              'engine_lock_sha256': hashlib.sha256((ROOT / 'requirements-engine-macos-arm64.txt').read_bytes()).hexdigest()}
    (release / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    if make_dmg:
        staging = work / 'image'
        staging.mkdir(exist_ok=True)
        staged_app = staging / app.name
        if staged_app.exists():
            shutil.rmtree(staged_app)
        shutil.copytree(app, staged_app, symlinks=True)
        applications = staging / 'Applications'
        if not applications.exists():
            applications.symlink_to('/Applications')
        dmg = release / f'MarcadoresDePaula-{__version__}-macOS-arm64-{edition}.dmg'
        run('hdiutil', 'create', '-ov', '-volname', 'Marcadores de Paula', '-srcfolder', staging,
            '-format', 'UDZO', dmg)
        (dmg.with_suffix('.dmg.sha256')).write_text(hashlib.sha256(dmg.read_bytes()).hexdigest() + '  ' + dmg.name + '\n')
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-manifest', type=Path,
                        help='Bundle the model pack described by this manifest as the default model.')
    parser.add_argument('--model-archive', type=Path,
                        help='Model pack ZIP; defaults to release/ and the archive named in the manifest.')
    parser.add_argument('--tools', type=Path, default=ROOT / 'tools/macos')
    parser.add_argument('--app-only', action='store_true')
    parser.add_argument('--release-dir', type=Path,
                        help='Use a separate destination for a controlled candidate build.')
    parser.add_argument('--work-dir', type=Path,
                        help='Use a separate intermediate directory for a candidate build.')
    parser.add_argument('--cocoa-bundle', type=Path,
                        help='Prepared, matching Cocoa accessibility repair and source bundle.')
    arguments = parser.parse_args()
    print(build(arguments.tools.resolve(), not arguments.app_only,
                arguments.release_dir.resolve() if arguments.release_dir else None,
                arguments.work_dir.resolve() if arguments.work_dir else None,
                arguments.cocoa_bundle.resolve() if arguments.cocoa_bundle else None,
                arguments.model_manifest.resolve() if arguments.model_manifest else None,
                arguments.model_archive.resolve() if arguments.model_archive else None))
