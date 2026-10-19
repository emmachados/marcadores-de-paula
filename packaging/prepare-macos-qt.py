"""Build the narrowly patched Qt Cocoa plugin from verified official Qt archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1] if SCRIPT.parent.name == 'packaging' else SCRIPT.parent
RECIPE = ROOT / 'packaging/qt-cocoa' if SCRIPT.parent.name == 'packaging' else SCRIPT.parent


def run(*command, **kwargs):
    print(' '.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(cmake, ninja, work, output):
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('The Cocoa repair must be built natively on Apple silicon.')
    manifest = json.loads((RECIPE / 'manifest.json').read_text())
    for executable, expected in ((cmake, manifest['cmake_version']), (ninja, manifest['ninja_version'])):
        version = subprocess.check_output([str(executable), '--version'], text=True).splitlines()[0]
        if expected not in version:
            raise RuntimeError(f'Expected build tool version {expected}, found {version}')
    patch = RECIPE / 'ownership.patch'
    if sha256(patch) != manifest['patch_sha256']:
        raise RuntimeError('The upstream patch does not match its recorded SHA-256.')
    archives = ROOT / 'build/macos-sources'
    archives.mkdir(parents=True, exist_ok=True)
    source = archives / 'qtbase-everywhere-src-6.12.0.tar.xz'
    sdk_archive = archives / 'qtbase-sdk-6.12.0.7z'
    for path, kind in ((source, 'source'), (sdk_archive, 'sdk')):
        if not path.exists() and (RECIPE / path.name).exists():
            shutil.copy2(RECIPE / path.name, path)
        if not path.exists():
            run('curl', '--fail', '--location', '--silent', '--show-error',
                manifest[kind + '_url'], '-o', path)
        if sha256(path) != manifest[kind + '_sha256']:
            raise RuntimeError(f'{path.name} failed its SHA-256 check.')
    work.mkdir(parents=True, exist_ok=True)
    sdk = work / 'sdk'
    sdk.mkdir(exist_ok=True)
    if not (sdk / 'lib/cmake/Qt6/Qt6Config.cmake').exists():
        run('tar', '-xf', sdk_archive, '-C', sdk)
    source_dir = work / 'qtbase-everywhere-src-6.12.0'
    if not source_dir.exists():
        run('tar', '-xf', source, '-C', work)
    stamp = work / 'applied-patch.sha256'
    if not stamp.exists():
        run('patch', '-p1', '-i', patch, cwd=source_dir)
        stamp.write_text(manifest['patch_sha256'] + '\n')
    elif stamp.read_text().strip() != manifest['patch_sha256']:
        raise RuntimeError('The work directory contains a different patch; use a new --work-dir.')
    build = work / 'build'
    run(cmake, '-S', RECIPE, '-B', build, '-G', 'Ninja',
        f'-DCMAKE_MAKE_PROGRAM={ninja}', f'-DCMAKE_PREFIX_PATH={sdk}',
        f'-DQTBASE_SOURCE_DIR={source_dir}', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_OSX_DEPLOYMENT_TARGET=26.0', '-DCMAKE_BUILD_TYPE=Release',
        f'-DCMAKE_INSTALL_PREFIX={work / "install"}', '-DQT_BUILD_TESTS=OFF',
        '-DQT_BUILD_EXAMPLES=OFF', '-DQT_FEATURE_vulkan=OFF',
        '-DQT_SKIP_AUTO_PLUGIN_INCLUSION=ON', '-DQT_GENERATE_SBOM=OFF')
    run(cmake, '--build', build, '--parallel', str(min(os.cpu_count() or 2, 8)))
    output.mkdir(parents=True, exist_ok=True)
    plugin = output / 'libqcocoa.dylib'
    shutil.copy2(build / 'plugins/platforms/libqcocoa.dylib', plugin)
    run('install_name_tool', '-rpath', sdk / 'lib', '@loader_path/../../../..', plugin)
    for framework in ('QtCore', 'QtGui'):
        run('install_name_tool', '-change', f'@rpath/{framework}.framework/Versions/A/{framework}',
            f'@rpath/{framework}', plugin)
    run('codesign', '--force', '--sign', '-', plugin)
    run('codesign', '--verify', '--strict', plugin)
    load = subprocess.check_output(['otool', '-l', str(plugin)], text=True)
    if re.findall(r'\bminos ([0-9.]+)', load) != ['26.0']:
        raise RuntimeError('Unexpected Cocoa plugin deployment target.')
    links = subprocess.check_output(['otool', '-L', str(plugin)], text=True)
    external = [line.strip() for line in links.splitlines()[1:]
                if not line.strip().startswith(('/System/Library/', '/usr/lib/', '@rpath/Qt'))]
    if external:
        raise RuntimeError(f'Unexpected Cocoa plugin dependencies: {external}')
    manifest['plugin_sha256'] = sha256(plugin)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.copy2(source, output / source.name)
    shutil.copy2(patch, output / patch.name)
    shutil.copy2(__file__, output / 'prepare-macos-qt.py')
    shutil.copy2(RECIPE / 'CMakeLists.txt', output / 'CMakeLists.txt')
    shutil.copy2(RECIPE / 'README.md', output / 'README.md')
    shutil.copytree(source_dir / 'LICENSES', output / 'LICENSES', dirs_exist_ok=True)
    print(json.dumps({'plugin': str(plugin), 'sha256': manifest['plugin_sha256']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cmake', type=Path, default=Path(sys.executable).parent / 'cmake')
    parser.add_argument('--ninja', type=Path, default=Path(sys.executable).parent / 'ninja')
    parser.add_argument('--work-dir', type=Path, default=ROOT / 'build/qt-cocoa-6.12.0')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/qt6120-cocoa-package')
    args = parser.parse_args()
    prepare(args.cmake.resolve(), args.ninja.resolve(), args.work_dir.resolve(), args.output.resolve())
