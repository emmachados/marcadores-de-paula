#!/usr/bin/env bash
# Usage: upload-draft.sh EDITION [FILE...]; attaches files and this job's reports to draft v<version>.
# RUNNER_OS (Windows or macOS) names the verification archive.
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
python - "$RUNNER_OS" "$version" "$edition" <<'PY'
from pathlib import Path
import sys, zipfile
with zipfile.ZipFile(f'{sys.argv[1]}-{sys.argv[2]}-{sys.argv[3]}-verification.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in Path('release').rglob('*'):
        inside_app = any(part.endswith('.app') for part in path.parts)
        if path.is_file() and path.suffix in ('.json', '.png', '.log', '.sha256') and not inside_app:
            archive.write(path, path.relative_to('release'))
PY
gh release upload "$tag" "$@" "$RUNNER_OS-$version-$edition-verification.zip" --clobber
