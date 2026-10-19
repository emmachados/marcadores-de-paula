import csv
import json
from xml.etree import ElementTree as ET

import pytest
from docx import Document

from transcript_desk.exports import FORMATS, export_result, speaker_turns, timestamp


def test_all_formats_are_readable_and_preserve_labels(tmp_path, result):
    paths = export_result(result, tmp_path)
    assert len(paths) == len(FORMATS)
    assert json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8")) == result
    rows = list(csv.DictReader((tmp_path / "transcript.csv").open(encoding="utf-8-sig")))
    assert rows[0]["speaker_name"] == "Interviewer"
    assert rows[2]["speaker_name"] == "Unassigned"
    document = Document(tmp_path / "transcript.docx")
    assert any('"café"' in p.text for p in document.paragraphs)
    eaf = ET.parse(tmp_path / "transcript.eaf")
    assert len(eaf.findall(".//ALIGNABLE_ANNOTATION")) == 3
    assert eaf.find(".//ANNOTATION_VALUE").text == result["segments"][0]["text"]
    tg = (tmp_path / "transcript.TextGrid").read_text(encoding="utf-8")
    assert 'name = "Interviewer"' in tg
    assert '""café""' in tg
    assert "00:00:00,500 --> 00:00:01,800" in (tmp_path / "transcript.srt").read_text(encoding="utf-8")
    assert (tmp_path / "transcript.vtt").read_text(encoding="utf-8").startswith("WEBVTT\n\n")


def test_timestamp_carries_milliseconds():
    assert timestamp(59.9999) == "00:01:00,000"
    assert timestamp(3600.1, ".") == "01:00:00.100"


def test_csv_formula_escape_and_caption_markup(tmp_path, result):
    result["segments"][0]["text"] = "=SUM(A1:A2)"
    export_result(result, tmp_path, ["CSV", "VTT"])
    rows = list(csv.reader((tmp_path / "transcript.csv").open(encoding="utf-8-sig")))
    assert rows[1][-1].startswith("'=")
    assert "&lt;sometimes&gt;" in (tmp_path / "transcript.vtt").read_text(encoding="utf-8")


def test_textgrid_rejects_overlap_in_same_tier(tmp_path, result):
    result["segments"][1].update(start=1, speaker="SPEAKER_00")
    with pytest.raises(ValueError, match="Overlapping"):
        export_result(result, tmp_path, ["TextGrid"])


def test_word_turns_keep_untimed_words_and_unknown_speakers():
    source = {"segments": [{"start": 0, "end": 3, "text": "Hello there yes unknown",
                            "words": [{"word": "Hello", "start": 0, "end": .5, "speaker": "SPEAKER_00"},
                                      {"word": "there", "speaker": "SPEAKER_00"},
                                      {"word": "yes", "start": 1, "end": 2, "speaker": "SPEAKER_01"},
                                      {"word": "unknown", "start": 2, "end": 3}]}]}
    turns = speaker_turns(source)
    assert [t["text"] for t in turns] == ["Hello there", "yes", "unknown"]
    assert turns[-1]["speaker"] is None


def test_invalid_timing_rejected(tmp_path, result):
    result["segments"][0]["start"] = float("nan")
    with pytest.raises(ValueError, match="timestamps"):
        export_result(result, tmp_path)


def test_word_turns_preserve_original_spacing():
    source = {"segments": [{"start": 0, "end": 2, "text": "你好。",
                            "words": [{"word": "你", "start": 0, "end": .5, "speaker": "SPEAKER_00"},
                                      {"word": "好。", "start": .5, "end": 2, "speaker": "SPEAKER_00"}]}]}
    assert speaker_turns(source)[0]["text"] == "你好。"


def test_word_normalisation_mismatch_does_not_duplicate_text():
    source = {"segments": [{"start": 0, "end": 1, "text": "Hello world.",
                            "words": [{"word": "Hello!", "start": 0, "end": .5, "speaker": "SPEAKER_00"},
                                      {"word": "world.", "start": .5, "end": 1, "speaker": "SPEAKER_00"}]}]}
    assert speaker_turns(source)[0]["text"] == "Hello world."
