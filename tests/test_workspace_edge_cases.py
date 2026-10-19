"""Edge cases that involve several modules."""
import copy
import csv
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QWidget, QTableWidget
from docx import Document

from transcript_desk.annotations import create_range_annotation, ensure_turn_ids, fingerprint
from transcript_desk.export_options import annotation_tier_segments, add_document_content, export_analysis
from transcript_desk.project_archive import write_archive, read_archive, save_snapshot, list_snapshots
from transcript_desk.projects import media_in_project
from transcript_desk.review_storage import atomic_write, read_review
from transcript_desk.text_tools import find_matches, replace_matches
from transcript_desk.review import ReviewPlayer
from transcript_desk.audio_tools import AudioTools
from test_autosave import prepared


def source():
    return {"segments": [{"start": 0, "end": 2, "speaker": "A", "text": "A first end"},
                         {"start": 2, "end": 4, "speaker": "B", "text": "B second tail"}],
            "speaker_names": {"A": "Ana", "B": "Bea"}, "metadata": {}}


def ranged_source():
    result = source()
    result["annotations"] = [create_range_annotation(result, 0, 1, "Topic boundary")]
    return result


def test_multiturn_annotation_tiers_use_each_source_speaker_and_passage():
    result = ranged_source()
    rows, names = annotation_tier_segments(result)
    ordered = sorted(rows, key=lambda s: s["start"])
    assert names[ordered[0]["speaker"]].startswith("Ana")
    assert names[ordered[1]["speaker"]].startswith("Bea")
    assert [s["text"] for s in ordered] == [s["text"] for s in result["segments"]]


def test_multiturn_document_marks_source_passages():
    result = ranged_source()
    document = Document()
    add_document_content(document, result, {"marked_passages": True})
    for segment in result["segments"]:
        paragraph = next(p for p in document.paragraphs if p.text == segment["text"])
        assert any(run.font.highlight_color is not None for run in paragraph.runs)


def test_multiturn_concordance_uses_context_from_last_span(tmp_path):
    result = ranged_source()
    a = result["annotations"][0]
    a.update(start_char=2, end_char=7, quote="first\nsecond")
    a["spans"][0].update(start_char=2, end_char=7, quote="first")
    a["spans"][1].update(start_char=2, end_char=8, quote="second")
    export_analysis(result, tmp_path)
    with (tmp_path / "concordance.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert rows[0]["left_context"] == "A " and rows[0]["right_context"] == " end"
    assert rows[1]["left_context"] == "B " and rows[1]["right_context"] == " tail"
    assert [row["speaker"] for row in rows] == ["Ana", "Bea"]
    assert [row["quote"] for row in rows] == ["first", "second"]


def test_replacement_rejects_a_preview_from_a_different_turn_identity():
    result = source()
    result["segments"][1]["text"] = result["segments"][0]["text"]
    ensure_turn_ids(result)
    matches = find_matches(result, "first")[:1]
    result["segments"].reverse()
    with pytest.raises(ValueError, match="changed|source|turn"):
        replace_matches(result, matches, "replacement")


def test_portable_media_and_snapshot_resolve_after_import(tmp_path):
    folder = tmp_path / "source"
    folder.mkdir()
    media = folder / "source.wav"
    media.write_bytes(b"media fixture")
    result = source()
    result["metadata"]["review_media_path"] = str(media)
    atomic_write(folder / "result.json", result)
    save_snapshot(folder, result, "Before editing")
    archive = write_archive(folder, result, tmp_path / "project.paula.zip")
    imported = read_archive(archive, tmp_path / "imported")
    reopened = read_review(imported / "reviewed.json")
    assert media_in_project(reopened, imported) == (imported / "media" / media.name).resolve()
    snapshot = read_review(list_snapshots(imported)[0][2])
    assert media_in_project(snapshot, imported) == (imported / "media" / media.name).resolve()


def test_decoder_start_failure_allows_retry(qtbot, tmp_path, monkeypatch):
    import transcript_desk.audio_tools as module
    window = QWidget()
    window.player = ReviewPlayer(window)
    window.transcript = QTableWidget(0, 4, window)
    window.result = source()
    qtbot.addWidget(window)
    panel = AudioTools(window)
    media = tmp_path / "source.wav"
    media.write_bytes(b"source fixture")
    window.player.player.setSource(QUrl.fromLocalFile(str(media)))
    monkeypatch.setattr(module, "tool_path", lambda name: str(tmp_path / "missing-decoder"))
    panel.load_waveform()
    qtbot.waitUntil(lambda: "could not start" in panel.message.text().lower(), timeout=3000)
    assert panel.load_button.isEnabled()
    assert panel.process is None


def test_bookmark_action_preserves_a_live_text_draft(qtbot, tmp_path, monkeypatch):
    from transcript_desk.workspace_panel import WorkspaceController
    from PySide6.QtWidgets import QInputDialog
    window, _ = prepared(qtbot, tmp_path, source())
    controller = getattr(window, "workspace", None) or WorkspaceController(window)
    window.transcript.setCurrentCell(0, 3)
    window.transcript.editItem(window.transcript.item(0, 3))
    window.delegate.editor.setPlainText("Current uncommitted words")
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Here", True))
    controller.add_bookmark()
    assert window.result["segments"][0]["text"] == "Current uncommitted words"
    assert window.result["bookmarks"][0]["name"] == "Here"


def export_dialog(qtbot, saved):
    from transcript_desk.export_dialog import ExportDialog
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.result = source()
    parent.settings = SimpleNamespace(value=lambda name, default: json.dumps(saved))
    dialog = ExportDialog(parent)
    qtbot.addWidget(dialog)
    return dialog


@pytest.mark.parametrize("saved", [["invalid"], "invalid", 42, None, {"speakers": [[], {}], "grouped": "yes"}])
def test_malformed_saved_export_presets_do_not_create_a_broken_option(qtbot, saved):
    dialog = export_dialog(qtbot, saved)
    assert dialog.saved == {}
    assert dialog.preset.findText("Saved preset") == -1


def test_saved_export_preset_restores_speaker_filter(qtbot):
    dialog = export_dialog(qtbot, {"speakers": ["B"], "formats": ["TXT"], "grouped": True, "timestamps": False})
    dialog.preset.setCurrentIndex(dialog.preset.findText("Saved preset"))
    assert dialog.options()["speakers"] == ["B"]
    assert dialog.preview.toPlainText() == "Bea\nB second tail"
    assert [name for name, box in dialog.formats.items() if box.isChecked()] == ["TXT"]
    assert not dialog.preview_timer.isActive()


def test_unknown_saved_speakers_require_an_explicit_selection(qtbot):
    dialog = export_dialog(qtbot, {"speakers": ["old-project-speaker"]})
    dialog.preset.setCurrentIndex(dialog.preset.findText("Saved preset"))
    assert dialog.options()["speakers"] == []
    assert "unavailable" in dialog.message.text()
    dialog.export()
    assert "Select at least" in dialog.message.text()


def test_export_preview_bounds_text_without_copying_word_records(qtbot):
    dialog = export_dialog(qtbot, {})
    class OriginalWords:
        def __deepcopy__(self, memo):
            raise AssertionError("The preview must not deep-copy word evidence")
    result = dialog.window.result
    result["segments"][0]["words"] = OriginalWords()
    result["segments"][0]["text"] = "Long text " * 10000
    before = result["segments"][0]["text"]
    dialog.update_preview()
    assert len(dialog.preview.toPlainText()) <= 12020
    assert dialog.preview.toPlainText().endswith("[Preview truncated]")
    assert result["segments"][0]["text"] == before
