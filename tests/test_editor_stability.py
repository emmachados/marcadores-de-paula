"""Run actual delegate commits in a child process to detect native Qt failures."""
import os
import subprocess
import sys
from pathlib import Path


def test_repeated_live_editor_commits_survive(tmp_path):
    script = r'''
import json
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from transcript_desk.window import MainWindow
app = QApplication([])
w = MainWindow()
w.show()
w.activateWindow()
QTest.qWait(50)
folder = Path(sys.argv[1]) / 'project'
folder.mkdir(parents=True)
original = {'speaker_names': {'A': 'Original'}, 'segments': [
    {'start': i, 'end': i + .9, 'speaker': 'A', 'text': 'Well, café 🙂 ' + str(i)}
    for i in range(12)]}
(folder / 'result.json').write_text(json.dumps(original), encoding='utf-8')
assert w.open_project_folder(folder)
for iteration in range(40):
    row = iteration % 12
    w.transcript.setCurrentCell(row, 2)
    w.transcript.editItem(w.transcript.item(row, 2))
    app.processEvents()
    editor = w.delegate.editor
    assert editor is not None
    editor.setFocus()
    QTest.qWait(5)
    editor.setText('Speaker ' + str(iteration))
    w.highlight_position(row * 1000 + 500)
    QTest.keyClick(editor, Qt.Key_Return if iteration % 2 else Qt.Key_Tab)
    app.processEvents()
    w.finish_edit()
    from transcript_desk.exports import speaker_name
    assert speaker_name(w.result['segments'][row]['speaker'], w.result['speaker_names']) == 'Speaker ' + str(iteration)
    assert w.result['speaker_names']['A'] == 'Original'
    w.transcript.setCurrentCell(row, 3)
    w.transcript.editItem(w.transcript.item(row, 3))
    app.processEvents()
    editor = w.delegate.editor
    assert editor is not None
    editor.setFocus()
    editor.setPlainText('Edited café 🙂 ' + str(iteration))
    assert w.save_edits()
    app.processEvents()
    if iteration % 5 == 0:
        w.move_review_history(-1)
        w.move_review_history(1)
assert w.save_edits()
w.close()
app.processEvents()
print('editor commits survived')
'''
    child = tmp_path / 'editor_probe.py'
    child.write_text(script, encoding='utf-8')
    env = {**os.environ, 'QT_QPA_PLATFORM': os.environ.get('QT_QPA_PLATFORM', 'offscreen'),
           'TRANSCRIPT_DESK_DATA': str(tmp_path / 'state'), 'PYTHONFAULTHANDLER': '1',
           'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    completed = subprocess.run([sys.executable, str(child), str(tmp_path)], env=env,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert 'editor commits survived' in completed.stdout
