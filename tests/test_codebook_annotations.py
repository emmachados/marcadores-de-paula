import copy
import json

import pytest
from PySide6.QtCore import Qt, QItemSelectionModel
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog

from transcript_desk.annotations import (create_annotation, create_time_annotation, create_range_annotation,
                                         validate_annotations, migrate_annotations, invalidate, is_current,
                                         export_annotations, suggest)
from transcript_desk.codebook import (default_codebook, new_entry, ensure_codebook, resolve_entry,
                                      annotation_name, merge_codebook, read_codebook, write_codebook,
                                      validate_codebook)
from transcript_desk.annotation_panel import AnnotationPanel
from transcript_desk.codebook_dialog import CodebookDialog


def project():
    return {"segments": [{"start": 0, "end": 2, "speaker": "A", "text": "Bueno, sí."},
                         {"start": 2, "end": 4, "speaker": "B", "text": "Sí, claro."},
                         {"start": 5, "end": 8, "speaker": "A", "text": "Bueno, bueno."}],
            "speaker_names": {"A": "Ana", "B": "Bea"}, "metadata": {}}


def make_panel(qtbot):
    panel = AnnotationPanel()
    qtbot.addWidget(panel)
    panel.bind(project())
    panel.resize(1300, 900)
    panel.show()
    return panel


def select_passage(panel, start=0, end=5):
    cursor = panel.passage.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    panel.passage.setTextCursor(cursor)


def test_legacy_categories_migrate_with_empty_dates():
    result = project()
    a = create_annotation(result, 0, 0, 5, "Discourse marker")
    for field in ("turn_id", "label_id", "function_id", "certainty", "author", "created_at", "modified_at", "anchor_type"):
        a.pop(field)
    result["annotations"] = [a]
    result.pop("annotation_codebook")
    result["annotation_schema"] = 1
    for segment in result["segments"]:
        segment.pop("turn_id")
    validate_annotations(result)
    assert result["annotation_schema"] == 2
    assert a["label_id"] == "label:discourse-marker"
    assert a["created_at"] == a["modified_at"] == ""
    assert a["quote"] == "Bueno" and a["status"] == "confirmed"
    assert a["turn_id"] == result["segments"][0]["turn_id"]


def test_rename_keeps_identity_and_export_definitions(tmp_path):
    result = project()
    result["annotations"] = [create_annotation(result, 0, 0, 5, "Discourse marker")]
    identity = result["annotations"][0]["label_id"]
    entry = resolve_entry(result, "labels", identity)
    entry.update(name="Project marker", definition="Defined in this study", color="#123ABC")
    validate_annotations(result)
    assert result["annotations"][0]["label_id"] == identity
    assert annotation_name(result, result["annotations"][0]) == "Project marker"
    paths = export_annotations(result, tmp_path)
    saved = json.loads(paths[0].read_text())
    assert saved["annotations"][0]["label"] == "Project marker"
    assert saved["codebook"]["labels"][0]["definition"] == "Defined in this study"
    assert suggest(result) > 0
    assert all(a["label"] != "Discourse marker" for a in result["annotations"])


def test_custom_codebook_import_round_trip_and_duplicate_names(tmp_path):
    original = default_codebook()
    custom = new_entry("Repair sequence")
    custom.update(definition="Study-specific definition", shortcut="Alt+1", group="Repair", favourite=True)
    original["labels"].append(custom)
    path = tmp_path / "codebook.json"
    write_codebook(path, original)
    imported = read_codebook(path)
    merged = merge_codebook(default_codebook(), imported)
    assert merged == original
    assert len(merge_codebook(merged, imported)["labels"]) == len(merged["labels"])
    broken = copy.deepcopy(imported)
    broken["labels"].append(new_entry("Repair sequence"))
    with pytest.raises(ValueError, match="unique"):
        validate_codebook(broken)


def test_time_only_and_multiturn_annotations_invalidate_independently():
    result = project()
    timed = create_time_annotation(result, 0, .1, .4, "Breath", author="Reviewer")
    ranged = create_range_annotation(result, 0, 2, "Topic boundary")
    result["annotations"] = [timed, ranged]
    validate_annotations(result)
    assert timed["quote"] == "" and timed["anchor_type"] == "time"
    assert len(ranged["spans"]) == 3
    assert ranged["quote"] == "Bueno, sí.\nSí, claro.\nBueno, bueno."
    result["segments"][1]["text"] = "Corrected words"
    invalidate(result, 1)
    assert timed["status"] == "confirmed" and is_current(timed, result)
    assert ranged["status"] == "needs review" and not is_current(ranged, result)
    validate_annotations(result)


@pytest.mark.parametrize("spans", ["broken", {"turn_id": "bad"}, ["broken"], [None], [{"turn_id": []}]])
def test_malformed_spans_are_recoverable_validation_errors(spans):
    result = project()
    a = create_annotation(result, 0, 0, 5, "Discourse marker")
    a["spans"] = spans
    result["annotations"] = [a]
    with pytest.raises(ValueError, match="spans|span"):
        validate_annotations(result)


def test_candidate_forms_are_project_local_and_literal():
    result = project()
    ensure_codebook(result)["candidate_forms"] = ["sí", "a+b"]
    result["segments"][2]["text"] = "a+b aaab"
    assert suggest(result) == 3
    assert {a["quote"] for a in result["annotations"]} == {"sí", "Sí", "a+b"}


def test_panel_multilabel_filters_batch_duplicate_delete(qtbot):
    panel = make_panel(qtbot)
    select_passage(panel)
    panel.apply_labels(["label:discourse-marker", "label:emphasis"])
    assert len(panel.result["annotations"]) == 2
    assert panel.table.rowCount() == 2
    panel.table.selectAll()
    panel.batch_status("rejected")
    assert {a["status"] for a in panel.result["annotations"]} == {"rejected"}
    panel.label_filter.setCurrentIndex(panel.label_filter.findData("label:emphasis"))
    assert panel.table.rowCount() == 1
    panel.table.selectRow(0)
    panel.duplicate_selected()
    assert len(panel.result["annotations"]) == 3
    assert len({a["id"] for a in panel.result["annotations"]}) == 3
    panel.table.selectAll()
    panel.delete_selected()
    assert len(panel.result["annotations"]) == 1
    assert panel.table.rowCount() == 0
    panel.label_filter.setCurrentIndex(0)
    assert panel.table.rowCount() == 1
    panel.speaker_filter.setCurrentIndex(panel.speaker_filter.findData("B"))
    assert panel.table.rowCount() == 0


def test_panel_range_listening_certainty_and_note_debounce(qtbot):
    panel = make_panel(qtbot)
    panel.end_turn.setCurrentIndex(panel.end_turn.findData(2))
    panel.reviewer.setText("Paula")
    panel.add_turn_range()
    a = panel.result["annotations"][0]
    assert len(a["spans"]) == 3 and a["author"] == "Paula"
    with qtbot.waitSignal(panel.playRange) as signal:
        panel.listen.click()
    assert signal.args == [0.0, 8.0]
    panel.certainty.setCurrentText("Tentative")
    assert a["certainty"] == "Tentative" and a["status"] == "confirmed"
    panel.author.setFocus()
    panel.author.selectAll()
    qtbot.keyClicks(panel.author, "Updated reviewer")
    assert a["author"] == "Updated reviewer"
    with qtbot.waitSignal(panel.changed, timeout=1500):
        panel.note.setPlainText("Review this sequence")
    assert a["note"] == "Review this sequence"
    panel.search.setText("sequence")
    assert panel.table.rowCount() == 1
    panel.from_time.setValue(9)
    assert panel.table.rowCount() == 0


def test_batch_confirmation_does_not_confirm_stale_source(qtbot):
    panel = make_panel(qtbot)
    select_passage(panel)
    panel.add_selection()
    a = panel.result["annotations"][0]
    panel.result["segments"][0]["text"] = "Changed"
    invalidate(panel.result, 0)
    panel.table.selectAll()
    panel.batch_status("confirmed")
    assert a["status"] == "needs review"


def test_codebook_dialog_edits_working_copy_and_validates(qtbot):
    book = default_codebook()
    dialog = CodebookDialog(book)
    qtbot.addWidget(dialog)
    table = dialog.tables["labels"]
    table.item(0, 0).setText("Renamed marker")
    table.item(0, 5).setText("Discourse")
    table.item(0, 6).setCheckState(Qt.Checked)
    assert book["labels"][0]["name"] == "Discourse marker"
    collected = dialog.collect()
    assert collected["labels"][0]["id"] == book["labels"][0]["id"]
    assert collected["labels"][0]["group"] == "Discourse"
    assert collected["labels"][0]["favourite"]
    table.item(0, 2).setText("bad colour")
    dialog.save()
    assert dialog.result() != QDialog.Accepted
    assert "hexadecimal" in dialog.message.text()


def test_filtered_export_contains_only_selected_records_and_preserves_project(tmp_path):
    result = project()
    result["annotations"] = [create_annotation(result, 0, 0, 5, "Discourse marker"),
                             create_annotation(result, 1, 0, 2, "Response / acknowledgement")]
    result["annotations"][1]["status"] = "rejected"
    paths = export_annotations(result, tmp_path, statuses={"confirmed"})
    payload = json.loads(paths[0].read_text())
    assert len(payload["annotations"]) == 1
    assert len(result["annotations"]) == 2
    assert payload["codebook"]


def test_related_annotations_link_navigate_and_remove_deleted_references(qtbot):
    panel = make_panel(qtbot)
    select_passage(panel)
    panel.apply_labels(["label:discourse-marker", "label:emphasis"])
    first, second = panel.result["annotations"]
    panel.table.selectAll()
    panel.link_selected()
    assert first["related_ids"] == [second["id"]]
    assert second["related_ids"] == [first["id"]]
    panel.select_id(first["id"])
    assert panel.related.currentData() == second["id"]
    panel.go_to_related()
    assert panel.current_id == second["id"]
    panel.delete_selected()
    assert len(panel.result["annotations"]) == 1
    assert first["related_ids"] == []
    validate_annotations(panel.result)
