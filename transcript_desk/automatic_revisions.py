"""Automatic on-disk review revisions, visible in the existing snapshot browser."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from transcript_desk.review_storage import atomic_write

INTERVAL_SECONDS = 60
RETAINED_REVISIONS = 30


def record_revision(folder, result, *, force=False):
    """Retain changed manual saves and at most one autosave revision per minute.

    The newest two live checkpoints remain independent. Retention only removes
    files written by this function, never a user's named snapshots.
    """
    directory = Path(folder) / '.revisions'
    manifest_path = directory / '.automatic.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, ValueError):
        manifest = {}
    now = datetime.now(timezone.utc)
    try:
        age = (now - datetime.fromisoformat(manifest['created_at'])).total_seconds()
    except (ValueError, TypeError, KeyError):
        age = INTERVAL_SECONDS
    if not force and 0 <= age < INTERVAL_SECONDS:
        return None
    revision = copy.deepcopy(result)
    revision.pop('snapshot', None)
    metadata = revision.setdefault('metadata', {})
    for key in ('review_saved_at', 'review_save_kind'):
        metadata.pop(key, None)
    digest = hashlib.sha256(json.dumps(revision, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    if manifest.get('sha256') == digest:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    revision['snapshot'] = {'name': 'Automatic review ' + now.astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                            'created_at': now.isoformat(), 'kind': 'automatic', 'sha256': digest}
    target = directory / ('auto-' + now.strftime('%Y%m%dT%H%M%S%f') + '-' + uuid.uuid4().hex[:8] + '.json')
    atomic_write(target, revision)
    atomic_write(manifest_path, {'created_at': now.isoformat(), 'sha256': digest, 'file': target.name})
    for obsolete in sorted(directory.glob('auto-*.json'), reverse=True)[RETAINED_REVISIONS:]:
        # A manually named snapshot can never match this reserved file prefix.
        obsolete.unlink(missing_ok=True)
    return target
