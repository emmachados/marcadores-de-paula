import copy
import json
from pathlib import Path

import pytest
from PySide6.QtGui import QTextCursor
from transcript_desk.annotations import (create_annotation, suggest, invalidate,
                                         validate_annotations, export_annotations)
from transcript_desk.review_storage import load_review
from test_autosave import prepared


def sample():
    return {"segments": [{"start": 0, "end": 4, "text": "Bueno, yo yo creo, o sea, bueno."}],
            "metadata": {}, "speaker_names": {}}


def test_candidates_preserve_wording_and_rejected_decisions():
    result = sample()
    original = copy.deepcopy(result["segments"])
    assert suggest(result) == 4
    assert [{k: v for k, v in s.items() if k != "turn_id"} for s in result["segments"]] == original
    assert result["segments"][0]["turn_id"]
    assert {a["status"] for a in result["annotations"]} == {"suggested"}
    assert {a["function"] for a in result["annotations"]} == {"Unspecified"}
    result["annotations"][0]["status"] = "rejected"
    assert suggest(result) == 0
    assert result["annotations"][0]["status"] == "rejected"


def test_text_change_invalidates_annotation_without_relocating_it():
    result = sample()
    result["annotations"] = [create_annotation(result, 0, 0, 5, "Discourse marker")]
    result["segments"][0]["text"] = "Yo creo, bueno."
    with pytest.raises(ValueError, match="Changed text"):
        validate_annotations(result)
    invalidate(result, 0)
    validate_annotations(result)
    assert result["annotations"][0]["quote"] == "Bueno"
    assert result["annotations"][0]["status"] == "needs review"


def test_sidecars_keep_status_and_escape_spreadsheet_formulas(tmp_path):
    result = sample()
    suggest(result)
    result["annotations"][0]["note"] = "=1+1"
    paths = export_annotations(result, tmp_path)
    assert len(paths) == 2
    saved = json.loads(paths[0].read_text())
    assert saved["annotations"] == result["annotations"]
    assert "'=1+1" in paths[1].read_text(encoding="utf-8-sig")


def test_annotations_autosave_reload_and_stale_confirmation(qtbot, tmp_path):
    widget, output = prepared(qtbot, tmp_path, sample())
    widget.tabs.setCurrentWidget(widget.annotations)
    panel = widget.annotations
    panel.find_button.click()
    panel.table.selectRow(0)
    panel.status.setCurrentText("confirmed")
    panel.function.setCurrentText("Discourse organisation")
    panel.note.setPlainText("Check opening function against audio.")
    qtbot.waitUntil(lambda: (output / "autosave.json").exists())
    saved = load_review(output)[0]
    assert saved["annotations"][0]["note"] == "Check opening function against audio."
    assert saved["annotations"][0]["status"] == "confirmed"
    widget.tabs.setCurrentIndex(0)
    widget.transcript.item(0, 3).setText("Yo creo, bueno.")
    widget.tabs.setCurrentWidget(panel)
    panel.table.selectRow(0)
    panel.status.setCurrentText("confirmed")
    assert widget.result["annotations"][0]["status"] == "needs review"
    cursor = panel.passage.textCursor()
    cursor.setPosition(9)
    cursor.setPosition(14, QTextCursor.KeepAnchor)
    panel.passage.setTextCursor(cursor)
    panel.reattach.click()
    panel.status.setCurrentText("confirmed")
    assert widget.result["annotations"][0]["quote"] == "bueno"
    assert widget.save_edits()
    widget.load_result(widget.result_job)
    assert widget.result["annotations"][0]["status"] == "confirmed"
    assert json.loads((output / "result.json").read_text()) == sample()


def test_annotation_selection_handles_unicode_and_save_copy(qtbot, tmp_path):
    result = sample()
    result["segments"][0]["text"] = "😀 bueno"
    widget, output = prepared(qtbot, tmp_path, result)
    panel = widget.annotations
    widget.tabs.setCurrentWidget(panel)
    cursor = panel.passage.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(8, QTextCursor.KeepAnchor)
    panel.passage.setTextCursor(cursor)
    panel.add_button.click()
    assert widget.result["annotations"][0]["start_char"] == 2
    assert widget.result["annotations"][0]["quote"] == "bueno"
    assert widget.save_edits()
    panel.note.setPlainText("After manual save")
    qtbot.waitUntil(lambda: (output / "autosave.json").exists())
    assert load_review(output)[0]["annotations"][0]["note"] == "After manual save"


def test_uncommitted_transcript_autosave_invalidates_labels(qtbot, tmp_path):
    result = sample()
    suggest(result)
    widget, output = prepared(qtbot, tmp_path, result)
    widget.transcript.setCurrentCell(0, 3)
    widget.transcript.editItem(widget.transcript.item(0, 3))
    widget.delegate.editor.setPlainText("Different passage")
    qtbot.waitUntil(lambda: (output / "autosave.json").exists())
    saved = load_review(output)[0]
    assert all(a["status"] == "needs review" for a in saved["annotations"])
