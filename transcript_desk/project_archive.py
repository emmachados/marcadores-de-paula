"""Portable review archives and named review snapshots."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from transcript_desk.projects import media_in_project
from transcript_desk.review_storage import atomic_write, read_review


def save_snapshot(folder, result, name):
    if not name.strip():
        raise ValueError('Enter a name for this snapshot.')
    directory = Path(folder) / '.revisions'
    directory.mkdir(parents=True, exist_ok=True)
    revision = copy.deepcopy(result)
    revision['snapshot'] = {'name': name.strip(), 'created_at': datetime.now(timezone.utc).isoformat()}
    path = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
    atomic_write(path, revision)
    return path


def list_snapshots(folder):
    revisions = []
    for path in (Path(folder) / '.revisions').glob('*.json'):
        try:
            result = read_review(path)
            info = result.get('snapshot', {})
            revisions.append((info.get('created_at', ''), str(info.get('name', path.stem)), path))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(revisions, reverse=True)


def write_archive(folder, result, destination, include_media=True):
    folder, destination = Path(folder), Path(destination)
    revision = copy.deepcopy(result)
    media = media_in_project(result, folder) if include_media else None
    metadata = revision.setdefault('metadata', {})
    for key in ('media_path', 'review_media_path'):
        metadata.pop(key, None)
    if media:
        metadata['review_media_path'] = 'media/' + media.name
    original = folder / 'result.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.paula-archive-', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            archive.writestr('project.json', json.dumps({'format': 'paula-project', 'version': 1}, indent=2))
            archive.writestr('reviewed.json', json.dumps(revision, ensure_ascii=False, indent=2))
            if original.is_file():
                draft = read_review(original)
                draft_metadata = draft.setdefault('metadata', {})
                for key in ('media_path', 'review_media_path'):
                    draft_metadata.pop(key, None)
                if media:
                    draft_metadata['review_media_path'] = 'media/' + media.name
                archive.writestr('result.json', json.dumps(draft, ensure_ascii=False, indent=2))
            if media:
                archive.write(media, 'media/' + media.name)
            for _, _, path in list_snapshots(folder):
                snapshot = read_review(path)
                snapshot_metadata = snapshot.setdefault('metadata', {})
                for key in ('media_path', 'review_media_path'):
                    snapshot_metadata.pop(key, None)
                if media:
                    snapshot_metadata['review_media_path'] = 'media/' + media.name
                archive.writestr('.revisions/' + path.name, json.dumps(snapshot, ensure_ascii=False, indent=2))
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def read_archive(source, destination):
    """Extract only into a new folder, rejecting links and path traversal."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Choose a new folder for the imported project; existing folders are never overwritten.')
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        if 'project.json' not in names or 'reviewed.json' not in names:
            raise ValueError('This is not a Marcadores de Paula project archive.')
        manifest = json.loads(archive.read('project.json'))
        if manifest != {'format': 'paula-project', 'version': 1}:
            raise ValueError('This project archive version is not supported.')
        if len(names) != len(archive.infolist()):
            raise ValueError('The archive contains duplicate paths.')
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                    or ':' in member.filename or not path.parts
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('The archive contains an unsafe file path.')
            if member.file_size > 100 * 1024 ** 3:
                raise ValueError('An archive entry exceeds the 100 GB limit.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.paula-import-', dir=destination.parent) as temporary:
            archive.extractall(temporary)
            read_review(Path(temporary) / 'reviewed.json')
            os.rename(temporary, destination)
    return destination
