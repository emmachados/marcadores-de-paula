"""Back up work before a version change and import earlier queues."""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from transcript_desk import __version__
from transcript_desk.review_storage import atomic_write, load_review
from transcript_desk.runtime import validate_queue

STATE_FILE = 'work-state.json'
REVIEW_FILES = ('result.json', 'reviewed.json', 'autosave.json', 'autosave.previous.json')
PREFERENCE_KEYS = {'theme', 'device', 'output', 'export_preset', 'recent_projects'}


def safe_preferences(settings):
    """Credentials and the remember-token flag never enter preservation files."""
    return {key: settings.value(key) for key in settings.allKeys()
            if (key in PREFERENCE_KEYS or key.startswith('position/'))
            and isinstance(settings.value(key), str)}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _copy_file(source, destination):
    if source.is_symlink():
        raise ValueError('A preservation file is a symbolic link: ' + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def create_backup(root, settings, previous_version='unrecorded', destination_parent=None):
    """Copy the saved files byte for byte, leaving the originals untouched."""
    root = Path(root).resolve()
    backups = Path(destination_parent) if destination_parent else root / 'preservation-backups'
    backups.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    destination = backups / name
    with tempfile.TemporaryDirectory(prefix='.pending-', dir=backups) as temporary:
        stage = Path(temporary)
        queue = root / 'queue.json'
        jobs = []
        queue_error = ''
        if queue.is_file():
            _copy_file(queue, stage / 'queue.json')
            try:
                jobs = validate_queue(_read_json(queue))
            except (ValueError, KeyError, TypeError) as exc:
                queue_error = str(exc)
        else:
            atomic_write(stage / 'queue.json', [])
        projects, missing = {}, []
        for job in jobs:
            original = job['config']['output']
            if original in projects:
                continue
            folder = Path(original)
            if not folder.is_dir():
                missing.append(original)
                continue
            relative = 'projects/' + str(len(projects) + 1)
            copied = False
            for filename in REVIEW_FILES:
                path = folder / filename
                if path.is_file():
                    _copy_file(path, stage / relative / filename)
                    copied = True
            revisions = folder / '.revisions'
            if revisions.is_dir():
                if revisions.is_symlink():
                    raise ValueError('A revision folder is a symbolic link: ' + str(revisions))
                for path in revisions.glob('*.json'):
                    _copy_file(path, stage / relative / '.revisions' / path.name)
                    copied = True
            if copied:
                projects[original] = relative
        atomic_write(stage / 'preferences.json', safe_preferences(settings))
        atomic_write(stage / 'preservation.json', {
            'format': 'paula-preservation', 'version': 1, 'previous_version': previous_version,
            'application_version': __version__, 'created_at': datetime.now(timezone.utc).isoformat(),
            'projects': projects, 'missing_projects': missing, 'queue_error': queue_error,
            'contents': 'Saved review files and revisions, queue and non-secret preferences. Media and processing runtimes are excluded.',
        })
        os.rename(stage, destination)
    return destination


def prepare_upgrade(root, settings, version=__version__):
    """Create one safety copy per version transition before opening existing work."""
    root = Path(root)
    marker = root / STATE_FILE
    previous = _read_json(marker) if marker.exists() else {}
    if not isinstance(previous, dict):
        raise ValueError('The previous work-state record is unreadable.')
    if previous.get('application_version') == version:
        return None
    backup = None
    if (root / 'queue.json').exists() or safe_preferences(settings):
        backup = create_backup(root, settings, previous.get('application_version', 'unrecorded'))
    atomic_write(marker, {'application_version': version,
                          'last_backup': str(backup) if backup else previous.get('last_backup', '')})
    return backup


def startup_preservation(root, settings):
    """An unavailable safety copy leaves its marker unchanged and work accessible."""
    try:
        return prepare_upgrade(root, settings), ''
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, str(exc)


def previous_work(source, projects_root=None):
    """Read without quarantining the source; optionally reconnect moved recording folders."""
    source = Path(source).resolve()
    queue = source / 'queue.json'
    if not queue.is_file():
        raise ValueError('Choose an earlier application-data folder or preservation backup containing queue.json.')
    if queue.is_symlink():
        raise ValueError('The earlier queue must be a regular file, not a symbolic link.')
    jobs = validate_queue(_read_json(queue))
    manifest = source / 'preservation.json'
    mappings = {}
    if manifest.is_symlink():
        raise ValueError('The preservation manifest must not be a symbolic link.')
    if manifest.exists():
        info = _read_json(manifest)
        if not isinstance(info, dict) or info.get('format') != 'paula-preservation' or info.get('version') != 1:
            raise ValueError('This preservation backup version is unsupported.')
        mappings = info.get('projects', {})
        if not isinstance(mappings, dict):
            raise ValueError('The backup project map is invalid.')
    missing = []
    for job in jobs:
        original = job['config']['output']
        folder = Path(original)
        if original in mappings:
            relative = mappings[original]
            if not isinstance(relative, str):
                raise ValueError('The backup project path is invalid.')
            folder = (source / relative).resolve()
            if not folder.is_relative_to(source):
                raise ValueError('The backup contains an unsafe project path.')
        elif not folder.is_dir() and projects_root:
            basename = PureWindowsPath(original).name if '\\' in original else Path(original).name
            folder = Path(projects_root).resolve() / basename
        if folder.is_dir():
            # Invalid saved reviews cannot displace usable current work.
            has_review = any((folder / name).is_file() for name in REVIEW_FILES)
            if has_review:
                load_review(folder)
            job['config']['output'] = str(folder.resolve())
            if original in mappings and has_review:
                job.update(status='complete', imported_project=True,
                           message='Restored saved review from a preservation backup. Recognition has not been rerun.')
                job['config']['source_type'] = 'project'
                job['preservation_origin'] = str(folder)
                job['_restore_from_backup'] = str(folder)
        else:
            missing.append(original)
    preference_path = source / 'preferences.json'
    if preference_path.is_symlink():
        raise ValueError('The preference record must not be a symbolic link.')
    preferences = _read_json(preference_path) if preference_path.exists() else {}
    if not isinstance(preferences, dict):
        raise ValueError('The earlier preference record is invalid.')
    preferences = {key: value for key, value in preferences.items()
                   if key in PREFERENCE_KEYS or key.startswith('position/')}
    if any(not isinstance(value, str) for value in preferences.values()):
        raise ValueError('An earlier preference has an invalid value.')
    if preferences.get('theme', 'dark') not in ('dark', 'light') or preferences.get('device', 'cpu') not in ('cpu', 'cuda'):
        raise ValueError('An earlier preference uses an unsupported theme or processing device.')
    return jobs, preferences, missing


def merge_previous_work(store, jobs):
    """Restore safety copies into new folders; commit their queue entries together."""
    imported = validate_queue(copy.deepcopy(jobs))
    paths = {os.path.normcase(str(Path(job['config']['output']).resolve())) for job in store.jobs}
    origins = {job.get('preservation_origin') for job in store.jobs if job.get('preservation_origin')}
    identifiers = {job['id'] for job in store.jobs}
    added, restored, skipped = [], [], 0
    previous = store.jobs
    try:
        for job in imported:
            key = os.path.normcase(str(Path(job['config']['output']).resolve()))
            origin = job.get('preservation_origin')
            if key in paths or (origin and origin in origins):
                skipped += 1
                continue
            if job['id'] in identifiers:
                job['id'] = uuid.uuid4().hex[:12]
            source = job.pop('_restore_from_backup', None)
            if source:
                source = Path(source)
                parent = store.path.parent / 'imported-projects'
                parent.mkdir(parents=True, exist_ok=True)
                destination = parent / ('restored-' + uuid.uuid4().hex)
                with tempfile.TemporaryDirectory(prefix='.pending-', dir=parent) as temporary:
                    stage = Path(temporary)
                    for filename in REVIEW_FILES:
                        path = source / filename
                        if path.is_file():
                            _copy_file(path, stage / filename)
                    revisions = source / '.revisions'
                    if revisions.is_symlink():
                        raise ValueError('The backup revision folder must not be a symbolic link.')
                    for path in revisions.glob('*.json'):
                        _copy_file(path, stage / '.revisions' / path.name)
                    if load_review(stage)[0] is None:
                        raise ValueError('The restored backup contains no readable saved review.')
                    os.rename(stage, destination)
                restored.append(destination)
                job['config']['output'] = str(destination.resolve())
            paths.add(os.path.normcase(str(Path(job['config']['output']).resolve())))
            if origin:
                origins.add(origin)
            identifiers.add(job['id'])
            added.append(job)
        if added:
            store.jobs = [*previous, *added]
            store.save()
    except (OSError, ValueError, KeyError, TypeError):
        store.jobs = previous
        for destination in restored:
            shutil.rmtree(destination)
        raise
    return len(added), skipped
