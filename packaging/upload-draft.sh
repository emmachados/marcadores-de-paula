#!/usr/bin/env bash
# Usage: upload-draft.sh EDITION [FILE...]; attaches files and this job's reports to draft v<version>.
set -euo pipefail
edition=$1; shift
version=$(python -c "from transcript_desk import __version__; print(__version__)")
tag="v$version"
gh release view "$tag" --json isDraft,targetCommitish > release.json
python - <<'PY'
import json, os
report = json.load(open('release.json'))
assert report['isDraft'], 'Refusing to modify a published release'
assert report['targetCommitish'] == os.environ['GITHUB_SHA'], 'Draft targets a different source commit'
PY
python - "$version" "$edition" <<'PY'
from pathlib import Path
import sys, zipfile
with zipfile.ZipFile(f'Windows-{sys.argv[1]}-{sys.argv[2]}-verification.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in Path('release').rglob('*'):
        if path.is_file() and path.suffix in ('.json', '.png', '.log', '.sha256'):
            archive.write(path, path.relative_to('release'))
PY
gh release upload "$tag" "$@" "Windows-$version-$edition-verification.zip" --clobber
