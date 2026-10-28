import json
import shutil
import pytest

from PySide6.QtCore import QSettings

from transcript_desk.exports import speaker_name
from transcript_desk.runtime import QueueStore
from transcript_desk.theme import stylesheet
from transcript_desk.window import MainWindow
from test_worker import make_wav


def window(qtbot, tmp_path):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    widget = MainWindow()
    widget.setStyleSheet(stylesheet())
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_batch_input_deduplicates_and_snapshots_options(qtbot, tmp_path):
    widget = window(qtbot, tmp_path)
    widget.urls.setPlainText("https://example.com/one\nhttps://example.com/two\nhttps://example.com/one")
    widget.add_links.click()
    assert len(widget.store.jobs) == 2
    widget.model.setCurrentIndex(1)
    assert widget.store.jobs[0]["config"]["model"] == "large-v3-turbo"
    assert widget.queue.rowCount() == 2
    widget.grab().save(str(tmp_path / "batch.png"))


def test_text_layout_choice_is_kept_with_each_recording(qtbot, tmp_path):
    widget = window(qtbot, tmp_path)
    widget.layout_actions["timestamps"].setChecked(False)
    widget.layout_actions["grouped"].setChecked(True)
    widget.urls.setPlainText("https://example.com/one")
    widget.add_links.click()
    assert widget.store.jobs[0]["config"]["text_layout"] == {"timestamps": False, "grouped": True, "paragraphs": True}
    widget.want_transcript.setChecked(False)
    assert not widget.layout_button.isEnabled()


def test_bundled_model_pack_default_preserves_queued_choices(qtbot, tmp_path, monkeypatch):
    import transcript_desk.window as module
    folder = tmp_path / "models/example-pack"
    folder.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
        (folder / name).write_text("fixture")
    (folder / "paula-model.json").write_text(json.dumps({
        "schema": 1, "name": "Example Spanish model", "base_model": "large-v3-turbo",
        "language": "es", "evaluation": {}, "training_corpus": "fixture"}))
    monkeypatch.setattr(module, "resource_root", lambda: tmp_path)
    widget = window(qtbot, tmp_path)
    assert widget.model.currentData() == "custom"
    assert widget.language.currentData() == "es"
    assert not widget.language.isEnabled()
    widget.add_sources(["https://example.com/bundled"], "url")
    widget.model.setCurrentIndex(0)
    assert widget.language.isEnabled()
    widget.add_sources(["https://example.com/stock"], "url")
    jobs = widget.store.jobs
    assert jobs[0]["config"]["custom_model_path"] == str(folder)
    assert jobs[0]["config"]["model"] == "custom"
    assert jobs[1]["config"]["model"] == "large-v3-turbo"
    widget.close()
    reopened = window(qtbot, tmp_path)
    assert reopened.model.currentData() == "custom"
    assert [j["config"]["model"] for j in reopened.store.jobs] == ["custom", "large-v3-turbo"]
    reopened.close()
    (tmp_path / "stock-default").write_text("large-v3-turbo")
    stock = window(qtbot, tmp_path)
    assert stock.model.currentData() == "large-v3-turbo"
    assert stock.model.findData("custom") >= 0  # Retain the model for earlier queue entries.
    assert stock.store.jobs[0]["config"]["model"] == "custom"


def test_queue_restore_preserves_queued_jobs_and_marks_running_jobs_interrupted(tmp_path):
    path = tmp_path / "queue.json"
    store = QueueStore(path)
    store.add("https://example.com/a", "url", {"output": str(tmp_path)})
    assert QueueStore(path).jobs[0]["status"] == "queued"
    store.jobs[0]["status"] = "running"
    store.save()
    restored = QueueStore(path)
    assert restored.jobs[0]["status"] == "interrupted"


def test_review_rename_and_save_preserves_original(qtbot, tmp_path, result):
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path))
    widget.add_sources(["https://example.com/a"], "url")
    job = widget.store.jobs[0]
    job["status"] = "complete"
    from pathlib import Path
    directory = Path(job["config"]["output"])
    directory.mkdir()
    original = directory / "result.json"
    original.write_text(json.dumps(result), encoding="utf-8")
    widget.refresh_queue()
    widget.queue.selectRow(0)
    widget.transcript.item(0, 2).setText("Researcher")
    widget.transcript.item(1, 3).setText("Corrected text.")
    assert widget.dirty
    assert widget.save_edits()
    reviewed = json.loads((directory / "reviewed.json").read_text(encoding="utf-8"))
    assert speaker_name(reviewed["segments"][0]["speaker"], reviewed["speaker_names"]) == "Researcher"
    assert reviewed["speaker_names"]["SPEAKER_00"] == "Interviewer"
    assert reviewed["segments"][1]["text"] == "Corrected text."
    assert json.loads(original.read_text(encoding="utf-8")) == result


def test_live_queue_completes_two_audio_jobs(qtbot, tmp_path):
    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("FFmpeg not installed")
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path / "results"))
    widget.want_transcript.setChecked(False)
    paths = [tmp_path / "first.wav", tmp_path / "second.wav"]
    for path in paths:
        make_wav(path)
    widget.add_sources([str(p) for p in paths], "file")
    widget.start_button.click()
    qtbot.waitUntil(lambda: not widget.running and widget.active is None, timeout=30000)
    assert [j["status"] for j in widget.store.jobs] == ["complete", "complete"]


def test_missing_engine_keeps_queue_pending(qtbot, tmp_path):
    widget = window(qtbot, tmp_path)
    widget.add_sources(["https://example.com/a"], "url")
    widget.start_queue()
    assert not widget.running
    assert widget.store.jobs[0]["status"] == "queued"


def test_failed_media_does_not_stop_following_recording(qtbot, tmp_path):
    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("FFmpeg not installed")
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path / "results"))
    widget.want_transcript.setChecked(False)
    good = tmp_path / "present.wav"
    make_wav(good)
    widget.add_sources([str(tmp_path / "missing.wav"), str(good)], "file")
    widget.start_queue()
    qtbot.waitUntil(lambda: not widget.running and widget.active is None, timeout=30000)
    assert [j["status"] for j in widget.store.jobs] == ["failed", "complete"]
    widget.queue.selectRow(0)
    widget.retry_selected()
    assert widget.store.jobs[0]["status"] == "queued"


def test_cancel_stops_current_process_and_leaves_pending_jobs(qtbot, tmp_path, monkeypatch):
    import sys
    import transcript_desk.window as module
    script = tmp_path / "slow.py"
    script.write_text("import time\nprint('{\"event\":\"progress\",\"stage\":\"Waiting\",\"percent\":null}', flush=True)\ntime.sleep(30)\n")
    monkeypatch.setattr(module, "worker_command", lambda *args: [sys.executable, str(script)])
    monkeypatch.setattr(module, "tool_path", lambda name: sys.executable)
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path / "results"))
    widget.want_transcript.setChecked(False)
    widget.add_sources(["https://example.com/a", "https://example.com/b"], "url")
    widget.start_queue()
    qtbot.waitUntil(lambda: widget.message.text() == "Waiting", timeout=10000)
    widget.cancel_current()
    qtbot.waitUntil(lambda: widget.active is None, timeout=10000)
    assert not widget.running
    assert [j["status"] for j in widget.store.jobs] == ["cancelled", "queued"]


@pytest.mark.parametrize("close_while_running", [False, True])
def test_pause_resume_survives_reopening_and_keeps_output_folder(qtbot, tmp_path, monkeypatch, close_while_running):
    import sys
    import transcript_desk.window as module
    script = tmp_path / 'resumable_fixture.py'
    script.write_text('''import json, pathlib, sys, time
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
folder = pathlib.Path(config['output'])
folder.mkdir(parents=True, exist_ok=True)
marker = folder / 'first-attempt'
if not marker.exists():
    marker.write_text('completed fixture stage')
    print(json.dumps({'event': 'progress', 'stage': 'Fixture processing', 'percent': None}), flush=True)
    time.sleep(30)
print(json.dumps({'event': 'complete', 'title': 'Fixture', 'output': str(folder)}), flush=True)
''')
    monkeypatch.setattr(module, 'worker_command', lambda config_path, *args: [sys.executable, str(script), str(config_path)])
    monkeypatch.setattr(module, 'tool_path', lambda _: sys.executable)
    widget = window(qtbot, tmp_path)
    widget.output.setText(str(tmp_path / 'outputs'))
    widget.want_transcript.setChecked(False)
    widget.add_sources(['https://example.com/one'], 'url')
    widget.queue.selectRow(0)
    output = widget.store.jobs[0]['config']['output']
    widget.start_queue()
    qtbot.waitUntil(lambda: widget.message.text() == 'Fixture processing', timeout=10000)
    widget.close() if close_while_running else widget.pause_button.click()
    qtbot.waitUntil(lambda: widget.active is None, timeout=10000)
    assert widget.store.jobs[0]['status'] == 'paused'
    assert widget.resume_button.isEnabled()
    widget.close()
    reopened = window(qtbot, tmp_path)
    reopened.queue.selectRow(0)
    assert reopened.store.jobs[0]['status'] == 'paused'
    reopened.resume_button.click()
    qtbot.waitUntil(lambda: reopened.active is None and not reopened.running, timeout=10000)
    assert reopened.store.jobs[0]['status'] == 'complete'
    assert reopened.store.jobs[0]['config']['output'] == output
    assert not reopened.store.jobs[0].get('previous_outputs')


def test_pause_during_cleanup_keeps_completed_recording(qtbot, tmp_path, monkeypatch):
    import sys
    import transcript_desk.window as module
    script = tmp_path / 'completed_worker.py'
    script.write_text('''import json, time
print(json.dumps({'event': 'complete', 'title': 'Finished', 'output': 'fixture'}), flush=True)
print(json.dumps({'event': 'progress', 'stage': 'Cleaning processing files', 'percent': None}), flush=True)
time.sleep(30)
''')
    monkeypatch.setattr(module, 'worker_command', lambda *args: [sys.executable, str(script)])
    monkeypatch.setattr(module, 'tool_path', lambda _: sys.executable)
    widget = window(qtbot, tmp_path)
    widget.want_transcript.setChecked(False)
    widget.add_sources(['https://example.com/finished'], 'url')
    widget.start_queue()
    qtbot.waitUntil(lambda: widget.message.text() == 'Cleaning processing files', timeout=10000)
    widget.pause_button.click()
    qtbot.waitUntil(lambda: widget.active is None, timeout=10000)
    assert widget.store.jobs[0]['status'] == 'complete'
