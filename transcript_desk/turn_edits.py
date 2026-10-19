"""Manual turn corrections, retaining text and annotation provenance."""
import copy
import math
import uuid

from transcript_desk.annotations import fingerprint, is_current, validate_annotations, ensure_turn_ids, sync_annotation_turn_ids


def stale(annotation):
    if annotation['status'] != 'needs review':
        annotation['previous_status'] = annotation['status']
    annotation['status'] = 'needs review'


def unalign(segment):
    if 'words' in segment:
        segment['original_words'] = segment.pop('words')
    segment['timing_edited'] = True


def assign_speaker(result, row, speaker):
    segment = result['segments'][row]
    previous = segment.get('speaker')
    segment.setdefault('original_speaker', segment.get('speaker'))
    segment['speaker'] = speaker
    if (previous and previous != speaker and previous.startswith('MANUAL_')
            and previous not in result.get('speaker_profiles', {})
            and all(s.get('speaker') != previous for s in result['segments'])):
        # A typed name that no turn uses any more, such as a corrected typo.
        result.get('speaker_names', {}).pop(previous, None)
    for word in segment.get('words', []):
        word.setdefault('original_speaker', word.get('speaker'))
        word['speaker'] = speaker
    _assign_word_copies(result, segment, speaker)
    for annotation in result.get('annotations', []):
        if annotation['segment'] == row or any(s.get('turn_id') == segment.get('turn_id') for s in annotation.get('spans', [])):
            stale(annotation)


def _word_key(word):
    return (word.get('start'), word.get('end'), word.get('word', '').strip())


def _assign_word_copies(result, segment, speaker):
    """Keep the recognition word lists, also written to JSON exports, in step with turns."""
    # After a split both halves keep every original word; take those inside this turn.
    keys = {_word_key(w) for w in segment.get('words', [])} | {
        _word_key(w) for w in segment.get('original_words', [])
        if w.get('start') is not None and segment['start'] <= w['start'] < segment['end']}
    keys = {key for key in keys if key[0] is not None}
    if not keys:
        return
    copies = result.get('word_segments', []) + [w for s in result.get('original_segments', []) for w in s.get('words', [])]
    for word in copies:
        if word.get('speaker') != speaker and _word_key(word) in keys:
            word.setdefault('original_speaker', word.get('speaker'))
            word['speaker'] = speaker


def speaker_for_name(result, name):
    """Return the identity displayed with this name, creating one only for a new name."""
    from transcript_desk.exports import speaker_name
    names = result.setdefault('speaker_names', {})
    identities = list(dict.fromkeys([s.get('speaker') for s in result['segments'] if s.get('speaker')] + list(names)))
    key = name.strip().casefold()
    match = next((i for i in identities if speaker_name(i, names).strip().casefold() == key), None)
    if match is None:
        match = 'MANUAL_' + uuid.uuid4().hex[:12]
        names[match] = name.strip()
    return match


def assign_speaker_name(result, row, name):
    """Assign one turn to the speaker with this display name; other turns keep theirs."""
    if not name.strip():
        return False
    speaker = None if name.strip().casefold() == 'unassigned' else speaker_for_name(result, name)
    if result['segments'][row].get('speaker') == speaker:
        return False
    assign_speaker(result, row, speaker)
    return True


def set_bounds(result, row, start, end):
    if not math.isfinite(start + end) or not 0 <= start < end:
        raise ValueError('End must be later than start, and both times must be finite and non-negative.')
    segment = result['segments'][row]
    segment.setdefault('original_timing', [segment['start'], segment['end']])
    segment.update(start=start, end=end)
    unalign(segment)
    for annotation in result.get('annotations', []):
        if annotation['segment'] == row or any(s.get('turn_id') == segment.get('turn_id') for s in annotation.get('spans', [])):
            if annotation['timing'] == 'turn':
                if annotation.get('spans'):
                    _refresh_multi(result, annotation)
                else:
                    annotation.update(start=start, end=end)
            stale(annotation)


def split_turn(result, row, character, seconds):
    segment = result['segments'][row]
    text = segment['text']
    if not 0 < character < len(text) or not text[:character].strip() or not text[character:].strip():
        raise ValueError('Place the text cursor between two non-empty passages.')
    if not segment['start'] < seconds < segment['end']:
        raise ValueError('The split time must be inside the selected turn.')
    ensure_turn_ids(result)
    left, right = copy.deepcopy(segment), copy.deepcopy(segment)
    left.update(text=text[:character], end=seconds)
    right.update(text=text[character:], start=seconds)
    right['turn_id'] = uuid.uuid4().hex
    for part in (left, right):
        unalign(part)
        part['text_edited'] = True
    annotations = result.get('annotations', [])
    current = {a['id']: is_current(a, result) for a in annotations if a['segment'] == row}
    result['segments'][row:row + 1] = [left, right]
    for a in annotations:
        if a.get('spans'):
            changed = False
            revised_spans = []
            for span in a['spans']:
                if span['turn_id'] != segment['turn_id']:
                    revised_spans.append(span)
                    continue
                changed = True
                first, last = span['start_char'], span['end_char']
                if span['text_sha256'] != fingerprint(text) or text[first:last] != span['quote']:
                    revised_spans.append(span)
                    continue
                for part, begin, finish, offset in ((left, first, min(last, character), 0),
                                                    (right, max(first, character), last, character)):
                    if begin < finish:
                        revised_spans.append({'turn_id': part['turn_id'], 'start_char': begin - offset,
                                              'end_char': finish - offset, 'quote': text[begin:finish],
                                              'text_sha256': fingerprint(part['text'])})
            a['spans'] = revised_spans
            if changed:
                stale(a)
            _refresh_multi(result, a)
            continue
        if a['segment'] > row:
            a['segment'] += 1
        elif a['segment'] == row:
            if a.get('anchor_type') == 'time':
                if a['start'] >= seconds:
                    a['segment'] += 1
                stale(a)
                continue
            if current[a['id']] and a['end_char'] <= character:
                a['text_sha256'] = fingerprint(left['text'])
            elif current[a['id']] and a['start_char'] >= character:
                a['segment'] += 1
                a['start_char'] -= character
                a['end_char'] -= character
                a['text_sha256'] = fingerprint(right['text'])
            else:
                # Keep the full quote and old offsets for manual reattachment.
                stale(a)
            if a['timing'] == 'turn':
                target = result['segments'][a['segment']]
                a.update(start=target['start'], end=target['end'])
            stale(a)
    sync_annotation_turn_ids(result)
    validate_annotations(result)


def merge_turn(result, row):
    if not 0 <= row < len(result['segments']) - 1:
        raise ValueError('Select a turn with a following turn.')
    left, right = result['segments'][row:row + 2]
    if left.get('speaker') != right.get('speaker'):
        raise ValueError('Assign both turns to the same speaker before merging them.')
    ensure_turn_ids(result)
    separator = '' if not left['text'] or not right['text'] or left['text'][-1].isspace() or right['text'][0].isspace() else ' '
    offset = len(left['text']) + len(separator)
    merged = copy.deepcopy(left)
    merged.update(text=left['text'] + separator + right['text'],
                  start=min(left['start'], right['start']), end=max(left['end'], right['end']))
    merged['original_words'] = copy.deepcopy(left.get('words', left.get('original_words', [])) +
                                            right.get('words', right.get('original_words', [])))
    merged.pop('words', None)
    merged['text_edited'] = True
    merged['timing_edited'] = True
    annotations = result.get('annotations', [])
    current = {a['id']: is_current(a, result) for a in annotations if a['segment'] in (row, row + 1)}
    for a in annotations:
        if a.get('spans'):
            for span in a['spans']:
                if span['turn_id'] in (left['turn_id'], right['turn_id']):
                    source = left if span['turn_id'] == left['turn_id'] else right
                    if span['text_sha256'] == fingerprint(source['text']):
                        if source is right:
                            span['start_char'] += offset
                            span['end_char'] += offset
                        span['text_sha256'] = fingerprint(merged['text'])
                    span['turn_id'] = left['turn_id']
                    stale(a)
            continue
        if a['segment'] in (row, row + 1):
            if a.get('anchor_type') != 'time' and current[a['id']]:
                if a['segment'] == row + 1:
                    a['start_char'] += offset
                    a['end_char'] += offset
                a['text_sha256'] = fingerprint(merged['text'])
            a['segment'] = row
            if a['timing'] == 'turn':
                a.update(start=merged['start'], end=merged['end'])
            stale(a)
        elif a['segment'] > row + 1:
            a['segment'] -= 1
    result['segments'][row:row + 2] = [merged]
    for a in annotations:
        if a.get('spans'):
            _refresh_multi(result, a)
    sync_annotation_turn_ids(result)
    validate_annotations(result)


def _refresh_multi(result, annotation):
    spans = annotation.get('spans', [])
    by_id = {s.get('turn_id'): (i, s) for i, s in enumerate(result['segments'])}
    if not spans or any(span['turn_id'] not in by_id for span in spans):
        stale(annotation)
        return
    first = spans[0]
    row, segment = by_id[first['turn_id']]
    annotation.update(segment=row, turn_id=segment['turn_id'], start_char=first['start_char'],
                      end_char=first['end_char'], text_sha256=first['text_sha256'])
    if annotation.get('timing') == 'turn':
        annotation['start'] = min(by_id[span['turn_id']][1]['start'] for span in spans)
        annotation['end'] = max(by_id[span['turn_id']][1]['end'] for span in spans)
