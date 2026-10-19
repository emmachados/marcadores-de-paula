import copy
import json
import zipfile

import pytest

from transcript_desk.annotations import create_annotation
from transcript_desk.project_archive import read_archive, write_archive, list_snapshots, save_snapshot
from transcript_desk.review_storage import atomic_write, load_review
from transcript_desk.text_tools import find_matches, reading_view, replace_matches


def test_grouping_interleaved_speakers_preserves_sources(result):
    result['segments'].append({'speaker': 'SPEAKER_00', 'start': 5, 'end': 6, 'text': 'Final question.'})
    original = copy.deepcopy(result)
    text, spans = reading_view(result, speaker='SPEAKER_00', all_speakers=False, paragraphs=False)
    assert text == 'Interviewer\nThe word is "café". Final question.'
    assert [span['row'] for span in spans] == [0, 3]
    assert [text[span['start']:span['end']] for span in spans] == [s['text'] for s in (result['segments'][0], result['segments'][3])]
    assert result == original


def test_replace_preserves_unaffected_annotation_and_marks_affected(result):
    result['segments'][0]['text'] = 'Well café and café 🙂'
    first = create_annotation(result, 0, 0, 4, 'Discourse marker')
    second = create_annotation(result, 0, 5, 9, 'Uncertain speech')
    result['annotations'] = [first, second]
    matches = find_matches(result, 'café', whole_word=True)
    changed = replace_matches(result, matches, 'coffee')
    assert changed['segments'][0]['text'] == 'Well coffee and coffee 🙂'
    assert result['segments'][0]['text'] == 'Well café and café 🙂'
    assert changed['annotations'][0]['status'] == 'confirmed'
    assert changed['annotations'][1]['status'] == 'needs review'
    result['segments'][0]['text'] = 'Changed elsewhere'
    with pytest.raises(ValueError, match='Search again'):
        replace_matches(result, matches, 'coffee')


def test_portable_archive_and_snapshots(tmp_path, result):
    folder = tmp_path / 'original'
    folder.mkdir()
    audio = folder / 'audio.wav'
    audio.write_bytes(b'fixture media')
    result['metadata']['media_path'] = str(audio)
    atomic_write(folder / 'result.json', result)
    save_snapshot(folder, result, 'Before coding')
    assert list_snapshots(folder)[0][1] == 'Before coding'
    archive = write_archive(folder, result, tmp_path / 'project.paula.zip')
    imported = read_archive(archive, tmp_path / 'new project')
    loaded = load_review(imported)[0]
    assert loaded['metadata']['review_media_path'] == 'media/audio.wav'
    from transcript_desk.projects import media_in_project
    assert media_in_project(loaded, imported).read_bytes() == b'fixture media'
    assert list_snapshots(imported)[0][1] == 'Before coding'
    with pytest.raises(ValueError, match='new folder'):
        read_archive(archive, imported)


def test_archive_rejects_path_traversal(tmp_path, result):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('project.json', json.dumps({'format': 'paula-project', 'version': 1}))
        z.writestr('reviewed.json', json.dumps(result))
        z.writestr('../outside', 'unsafe')
    with pytest.raises(ValueError, match='unsafe'):
        read_archive(archive, tmp_path / 'new')
    assert not (tmp_path / 'outside').exists()
