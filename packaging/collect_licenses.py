"""Copies the licence files of the build environment's distributions and of Python itself."""
import importlib.metadata
import shutil
import sys
from pathlib import Path

NAMES = ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'AUTHORS')

target = Path(sys.argv[1])
copied = 0
for distribution in importlib.metadata.distributions():
    for file in distribution.files or []:
        path = str(file).replace('\\', '/')
        if '.dist-info/' not in path:
            continue
        if '.dist-info/licenses/' in path or Path(path).name.upper().startswith(NAMES):
            destination = target / distribution.metadata['Name'] / Path(path).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(distribution.locate_file(file), destination)
            copied += 1
base = Path(sys.base_prefix)
python_licence = next((path for path in (base / 'LICENSE.txt', base / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt')
                       if path.exists()), None)
if python_licence:
    (target / 'Python').mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_licence, target / 'Python' / 'LICENSE.txt')
    copied += 1
if not copied:
    raise SystemExit('No licence files were found in the build environment.')
print(f'Copied {copied} licence files to {target}')
