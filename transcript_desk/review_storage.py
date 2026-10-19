"""Saving and loading the review, kept apart from the original recognition output."""
import json
import math
import os
import tempfile
from pathlib import Path

from transcript_desk.file_replace import replace_file


def read_review(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_review(result)
    return result


def validate_review(result):
    """Validate and normalize a loaded review before any UI state is replaced."""
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        raise ValueError("Invalid review structure")
    if type(result.get('review_schema', 1)) is not int or result.get('review_schema', 1) != 1:
        raise ValueError('This review uses an unsupported schema version')

    def seconds(value):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError('Invalid transcript timing type')
        try:
            value = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError('Invalid transcript timing') from exc
        if not math.isfinite(value) or not 0 <= value <= 9_223_372_036_854:
            raise ValueError('Invalid transcript timing')
        return value

    for segment in result["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise ValueError("Invalid transcript segment")
        start, end = seconds(segment.get('start')), seconds(segment.get('end'))
        if end < start:
            raise ValueError("Invalid transcript timings")
        segment.update(start=start, end=end)
        if segment.get('speaker') is not None and not isinstance(segment['speaker'], str):
            raise ValueError('Invalid transcript speaker')
        for key in ('words', 'original_words'):
            words = segment.get(key, [])
            if not isinstance(words, list):
                raise ValueError('Invalid word records')
            for word in words:
                if not isinstance(word, dict) or not isinstance(word.get('word', ''), str):
                    raise ValueError('Invalid word record')
                for field in ('speaker', 'speaker_review'):
                    if word.get(field) is not None and not isinstance(word[field], str):
                        raise ValueError('Invalid word speaker record')
                for field in ('start', 'end'):
                    if word.get(field) is not None:
                        word[field] = seconds(word[field])
                if word.get('start') is not None and word.get('end') is not None and word['end'] < word['start']:
                    raise ValueError('Invalid word timings')
    if not isinstance(result.get("metadata", {}), dict) or not isinstance(result.get("speaker_names", {}), dict):
        raise ValueError("Invalid review metadata")
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in result.get('speaker_names', {}).items()):
        raise ValueError('Invalid speaker names')
    metadata = result.get('metadata', {})
    for field in ('title', 'media_path', 'review_media_path', 'review_saved_at'):
        if metadata.get(field) is not None and not isinstance(metadata[field], str):
            raise ValueError('Invalid review metadata field: ' + field)
    from transcript_desk.annotations import validate_annotations, ensure_turn_ids
    from transcript_desk.text_tools import validate_workspace_data
    ensure_turn_ids(result)
    validate_workspace_data(result)
    validate_annotations(result)
    return result


def atomic_write(path, result):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Preserve the original write error and the last valid file.


def save_checkpoint(output, result, automatic=False):
    output = Path(output)
    target = output / ("autosave.json" if automatic else "reviewed.json")
    if automatic and target.exists():
        try:
            previous = read_review(target)
        except (ValueError, KeyError, TypeError, OSError):
            previous = None
        if previous is not None:
            atomic_write(output / "autosave.previous.json", previous)
    atomic_write(target, result)


def load_review(output):
    output = Path(output)
    candidates = [output / name for name in ("reviewed.json", "autosave.json", "autosave.previous.json")]
    # The previous checkpoint was copied recently, so compare its recorded save time.
    valid = []
    failures = []
    for path in candidates:
        try:
            result = read_review(path)
            saved = result.get("metadata", {}).get("review_saved_at", "")
            valid.append((saved if isinstance(saved, str) else "", path.stat().st_mtime_ns, path, result))
        except FileNotFoundError:
            continue
        except (ValueError, KeyError, TypeError, OSError):
            failures.append(path.name)
    if valid:
        _, _, path, result = max(valid, key=lambda item: item[:2])
        return result, path, failures
    path = output / "result.json"
    if path.exists():
        return read_review(path), path, failures
    return None, None, failures
