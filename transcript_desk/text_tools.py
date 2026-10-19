"""Speaker-text views and find-and-replace operations on the review."""
from __future__ import annotations

import copy
import math
import re
from collections import Counter

from transcript_desk.annotations import fingerprint, invalidate
from transcript_desk.exports import speaker_name, timestamp


def validate_workspace_data(result):
    profiles = result.get('speaker_profiles', {})
    if not isinstance(profiles, dict):
        raise ValueError('Invalid speaker profiles.')
    for identity, profile in profiles.items():
        if (not isinstance(identity, str) or not isinstance(profile, dict)
                or not isinstance(profile.get('note', ''), str)
                or not isinstance(profile.get('color', ''), str)
                or not re.fullmatch(r'#[0-9a-fA-F]{6}', profile.get('color', '#4769B0'))):
            raise ValueError('Invalid speaker profile.')
    bookmarks = result.get('bookmarks', [])
    if not isinstance(bookmarks, list):
        raise ValueError('Invalid bookmarks.')
    for bookmark in bookmarks:
        if (not isinstance(bookmark, dict) or not isinstance(bookmark.get('name'), str)
                or type(bookmark.get('row')) is not int or bookmark['row'] < 0
                or bookmark.get('turn_id') is not None and not isinstance(bookmark['turn_id'], str)):
            raise ValueError('Invalid bookmark.')
        seconds = bookmark.get('seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Invalid bookmark time.')


def speaker_ids(result):
    return list(dict.fromkeys(s.get('speaker') for s in result.get('segments', [])))


def reading_view(result, *, speaker=None, all_speakers=True, timestamps=False,
                 paragraphs=True, grouped=True):
    """Return display text with the exact source span of each part."""
    rows = [(i, s) for i, s in enumerate(result.get('segments', []))
            if all_speakers or s.get('speaker') == speaker]
    rows.sort(key=lambda item: (item[1]['start'], item[1]['end'], item[0]))
    if grouped:
        identities = list(dict.fromkeys(s.get('speaker') for _, s in rows))
        rows = [(i, s) for identity in identities for i, s in rows if s.get('speaker') == identity]
    parts, spans, offset, previous = [], [], 0, object()
    for row, segment in rows:
        identity = segment.get('speaker')
        if identity != previous:
            heading = ('\n\n' if parts else '') + speaker_name(identity, result.get('speaker_names', {})) + '\n'
            parts.append(heading)
            offset += len(heading)
        elif parts:
            separator = '\n\n' if paragraphs else ' '
            parts.append(separator)
            offset += len(separator)
        if timestamps:
            prefix = f"[{timestamp(segment['start'], '.')} – {timestamp(segment['end'], '.')}] "
            parts.append(prefix)
            offset += len(prefix)
        text = segment['text']
        spans.append({'row': row, 'turn_id': segment.get('turn_id'), 'start': offset,
                      'end': offset + len(text), 'source_start': segment['start'], 'source_end': segment['end']})
        parts.append(text)
        offset += len(text)
        previous = identity
    return ''.join(parts), spans


def find_matches(result, query, *, case_sensitive=False, whole_word=False,
                 speaker=None, all_speakers=True, start=0, end=float('inf')):
    if not query:
        return []
    expression = re.escape(query)
    if whole_word:
        expression = r'(?<!\w)' + expression + r'(?!\w)'
    pattern = re.compile(expression, 0 if case_sensitive else re.IGNORECASE)
    matches = []
    for row, segment in enumerate(result.get('segments', [])):
        if (not all_speakers and segment.get('speaker') != speaker) or segment['end'] < start or segment['start'] > end:
            continue
        text = segment['text']
        for match in pattern.finditer(text):
            matches.append({'row': row, 'turn_id': segment.get('turn_id'), 'start': match.start(),
                            'end': match.end(), 'quote': match.group(), 'source_text': text,
                            'context': text[max(0, match.start() - 45):min(len(text), match.end() + 65)]})
    return matches


def _shift_anchor(anchor, changes, new_text, *, top_level=False):
    start, end = anchor['start_char'], anchor['end_char']
    affected = any(a < end and b > start for a, b, _ in changes)
    if affected:
        return False
    displacement = sum(len(value) - (b - a) for a, b, value in changes if b <= start)
    anchor['start_char'], anchor['end_char'] = start + displacement, end + displacement
    anchor['text_sha256'] = fingerprint(new_text)
    return True


def replace_matches(result, matches, replacement):
    """Validate the whole batch before touching text; callers commit one undo step."""
    revised = copy.deepcopy(result)
    by_row = {}
    for match in matches:
        row = match['row']
        segment = revised['segments'][row]
        if ((match.get('turn_id') and segment.get('turn_id') != match['turn_id'])
                or segment['text'] != match['source_text'] or segment['text'][match['start']:match['end']] != match['quote']):
            raise ValueError('The transcript changed after the preview. Search again before replacing.')
        by_row.setdefault(row, set()).add((match['start'], match['end']))
    for row, positions in by_row.items():
        segment = revised['segments'][row]
        changes = [(a, b, replacement) for a, b in sorted(positions)]
        if any(left[1] > right[0] for left, right in zip(changes, changes[1:])):
            raise ValueError('The replacement selection contains overlapping matches.')
        text = segment['text']
        for a, b, value in reversed(changes):
            text = text[:a] + value + text[b:]
        if text == segment['text']:
            continue
        segment['text'] = text
        segment['text_edited'] = True
        if 'words' in segment:
            segment['original_words'] = segment.pop('words')
        for annotation in revised.get('annotations', []):
            if annotation.get('anchor_type') == 'time':
                continue
            current = True
            if annotation.get('segment') == row:
                current = _shift_anchor(annotation, changes, text)
            for span in annotation.get('spans', []):
                if span.get('turn_id') == segment.get('turn_id'):
                    current = _shift_anchor(span, changes, text) and current
            if not current:
                if annotation['status'] != 'needs review':
                    annotation['previous_status'] = annotation['status']
                annotation['status'] = 'needs review'
        invalidate(revised, row)
    return revised


def speaker_statistics(result):
    rows = []
    for identity in speaker_ids(result):
        segments = [s for s in result['segments'] if s.get('speaker') == identity]
        rows.append({'speaker': identity, 'name': speaker_name(identity, result.get('speaker_names', {})),
                     'turns': len(segments), 'words': sum(len(re.findall(r"\w+(?:['’]\w+)*", s['text'])) for s in segments),
                     'seconds': sum(max(0, s['end'] - s['start']) for s in segments)})
    return rows


def annotation_counts(result, statuses=('confirmed',)):
    counts = Counter()
    for annotation in result.get('annotations', []):
        if statuses is not None and annotation.get('status') not in statuses:
            continue
        segment = result['segments'][annotation['segment']]
        counts[(segment.get('speaker'), annotation['label'])] += 1
    return counts
