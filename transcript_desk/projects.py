"""Reopen existing recording folders without rerunning recognition."""
from pathlib import Path, PureWindowsPath
import uuid

from transcript_desk.review_storage import load_review


def media_in_project(result, folder):
    metadata = result.get('metadata', {})
    for value in (metadata.get('review_media_path'), metadata.get('media_path')):
        if not value:
            continue
        path = Path(value)
        relative = Path(folder) / path
        if not path.is_absolute() and relative.is_file():
            return relative.resolve()
        # Prefer the copy beside the transcript when a project has moved.
        basename = PureWindowsPath(value).name if '\\' in value else path.name
        local = Path(folder) / basename
        if local.is_file():
            return local.resolve()
        if path.is_file():
            return path.resolve()
    return None


def project_job(folder):
    folder = Path(folder).resolve()
    result, _, _ = load_review(folder)
    if result is None:
        raise ValueError('This folder contains no readable result.json, reviewed.json or autosave checkpoint.')
    metadata = result.get('metadata', {})
    source = str(metadata.get('source') or folder)
    return {'id': uuid.uuid4().hex[:12], 'source': source, 'title': metadata.get('title') or folder.name,
            'status': 'complete', 'message': 'Opened saved recording. Recognition has not been rerun.',
            'imported_project': True,
            'config': {'source': source, 'source_type': 'project', 'output': str(folder),
                       'transcribe': True, 'save_audio': False, 'save_video': False,
                       'formats': ['TXT', 'JSON'], 'review_before_export': True}}
