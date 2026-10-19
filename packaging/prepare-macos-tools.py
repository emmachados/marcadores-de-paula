"""Copy external tools and relocate every non-system Mach-O dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def dependencies(path):
    output = subprocess.check_output(['otool', '-L', str(path)], text=True)
    return [line.strip().split(' (', 1)[0] for line in output.splitlines()[1:]]


def system_library(path):
    return path.startswith(('/System/Library/', '/usr/lib/'))


def bundle_tools(sources, destination):
    destination.mkdir(parents=True, exist_ok=True)
    library_dir = destination / 'lib'
    library_dir.mkdir(exist_ok=True)
    copied = {}

    def copy(source, target):
        source = source.resolve()
        if source in copied:
            return copied[source]
        if target.exists() and target.name not in sources:
            target.unlink()
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode | 0o200)
        copied[source] = target
        changes = []
        for dependency in dependencies(source):
            if system_library(dependency):
                continue
            if dependency.startswith('@loader_path/'):
                resolved = source.parent / dependency.removeprefix('@loader_path/')
            elif dependency.startswith('@rpath/') and Path(dependency).name == source.name:
                continue  # A dylib's own install name.
            elif dependency.startswith('@'):
                raise ValueError(f'Unresolved dependency {dependency} in {source}')
            else:
                resolved = Path(dependency)
            if resolved.resolve() == source:
                continue
            dep_target = copy(resolved, library_dir / resolved.name)
            relative = os.path.relpath(dep_target, target.parent)
            changes.extend(['-change', dependency, '@loader_path/' + relative])
        if target.suffix == '.dylib':
            changes += ['-id', '@rpath/' + target.name]
        if changes:
            subprocess.run(['install_name_tool', *changes, str(target)], check=True, capture_output=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(target)], check=True, capture_output=True)
        return target

    for name, path in sources.items():
        copy(Path(path), destination / name)
    manifest = {}
    for path in copied.values():
        unresolved = [item for item in dependencies(path) if item.startswith('/') and not system_library(item)]
        if unresolved:
            raise ValueError(f'External dependencies remain in {path}: {unresolved}')
        manifest[str(path.relative_to(destination))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg-prefix', type=Path, required=True)
    parser.add_argument('--uv', type=Path, required=True)
    parser.add_argument('--deno', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('tools/macos'))
    args = parser.parse_args()
    bundle_tools({'ffmpeg': args.ffmpeg_prefix / 'bin/ffmpeg', 'ffprobe': args.ffmpeg_prefix / 'bin/ffprobe',
                  'uv': args.uv, 'deno': args.deno}, args.output)
