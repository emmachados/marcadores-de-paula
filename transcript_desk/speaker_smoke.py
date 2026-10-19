"""Speaker corrections on a one-hour diarized project, with an accessibility client reading the table."""
import json
import os
import platform
import random
import subprocess
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, qVersion
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest

# One assignment took about 20 s on Apple silicon before column measurement was deferred.
OPERATION_LIMIT = 8.0

UIA_SCAN = r'''
param([int]$ProcessId, [int]$Seconds, [string]$Output)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$automation = [System.Windows.Automation.AutomationElement]
$condition = New-Object System.Windows.Automation.PropertyCondition($automation::ProcessIdProperty, $ProcessId)
$deadline = (Get-Date).AddSeconds($Seconds)
$scans = 0; $largest = 0
while ((Get-Date) -lt $deadline) {
    $window = $automation::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
    if ($window) {
        $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($element in $all) { try { $null = $element.Current.Name } catch {} }
        $scans += 1
        if ($all.Count -gt $largest) { $largest = $all.Count }
    }
    Start-Sleep -Milliseconds 100
}
@{scans = $scans; largest = $largest} | ConvertTo-Json | Set-Content -Encoding UTF8 $Output
'''


def diarized_hour(seed=1):
    from transcript_desk.exports import speaker_turns
    from transcript_desk.speaker_assignment import assign_speakers
    generator = random.Random(seed)
    words, intervals, now, speaker = [], [], 0.0, 'SPEAKER_00'
    while now < 3600:
        start = now
        for _ in range(generator.randint(3, 40)):
            words.append({'word': 'palabra', 'start': round(now, 3), 'end': round(now + .3, 3), 'score': .9})
            now += .4
        intervals.append((start, now, speaker))
        if generator.random() < .2:
            intervals.append((now - 1, now + 1, 'SPEAKER_03'))  # Overlap yields competing candidates.
        speaker = generator.choice(['SPEAKER_00', 'SPEAKER_01', 'SPEAKER_02'])
    segments = [{'start': words[i]['start'], 'end': words[min(i + 19, len(words) - 1)]['end'],
                 'text': ' '.join(w['word'] for w in words[i:i + 20]), 'words': [dict(w) for w in words[i:i + 20]]}
                for i in range(0, len(words), 20)]
    result = {'segments': segments, 'word_segments': [dict(w) for w in words], 'language': 'es', 'speaker_names': {},
              'metadata': {'title': 'Synthetic one-hour diarized recording', 'diarization': 'complete', 'duration': now}}
    result = assign_speakers(intervals, result)
    result['original_segments'] = result['segments']
    result['segments'] = speaker_turns(result)
    result['metadata']['speaker_intervals'] = [{'start': a, 'end': b, 'speaker': s} for a, b, s in intervals]
    return result


def run_speaker_check(application, window, report_path):
    from transcript_desk.exports import speaker_name
    from transcript_desk.review_storage import atomic_write, load_review
    from transcript_desk.runtime import data_root
    from transcript_desk import workspace_panel
    application.setQuitOnLastWindowClosed(False)
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    folder = data_root() / 'Synthetic speaker verification'
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write(folder / 'result.json', diarized_hour())
    status = {'synthetic_fixture': True, 'platform': platform.system(), 'qt': qVersion(),
              'operation_limit_seconds': OPERATION_LIMIT, 'timings': {}, 'checks': []}
    scanner, scan_output = None, report.with_name(report.stem + '-uia.json')
    steps = []

    def names():
        return [speaker_name(s.get('speaker'), window.result['speaker_names']) for s in window.result['segments']]

    def timed(name, action):
        started = time.perf_counter()
        action()
        application.processEvents()
        elapsed = round(time.perf_counter() - started, 3)
        status['timings'][name] = elapsed
        assert elapsed < OPERATION_LIMIT, f'{name} blocked the interface for {elapsed} s'

    def read_accessible_table():
        table = QAccessible.queryAccessibleInterface(window.transcript)
        assert table is not None and table.childCount() >= window.transcript.rowCount()
        for index in range(0, table.childCount(), max(1, table.childCount() // 400)):
            child = table.child(index)
            if child is not None:
                child.text(QAccessible.Name)

    def inline(row, text):
        window.transcript.setCurrentCell(row, 2)
        window.transcript.scrollToItem(window.transcript.item(row, 2))
        window.transcript.editItem(window.transcript.item(row, 2))
        application.processEvents()
        editor = window.delegate.editor
        assert editor is not None
        editor.setFocus()
        editor.setText(text)
        QTest.keyClick(editor, Qt.Key_Return)
        application.processEvents()
        window.finish_edit()

    def open_project():
        assert window.open_project_folder(folder)
        assert window.transcript.rowCount() > 600
        status['turns'] = window.transcript.rowCount()
        status['words'] = len(window.result['word_segments'])
        status['checks'].append('opened one-hour diarized project')

    def inline_edits():
        before = names()
        for row in (10, 200, 410, 620):
            timed(f'speaker cell row {row}', lambda row=row: inline(row, 'Paula'))
        after = names()
        changed = [row for row, (old, new) in enumerate(zip(before, after)) if old != new]
        assert set(changed) <= {10, 200, 410, 620} and {after[row] for row in (10, 200, 410, 620)} == {'Paula'}
        assert len({window.result['segments'][row]['speaker'] for row in (10, 200, 410, 620)}) == 1
        status['checks'].append('speaker cell changes one turn and reuses a typed name')

    def dialog_edits():
        for row in (30, 300, 600):
            timed(f'assign row {row}', lambda row=row: window.apply_turn_edit('speaker', row, 'SPEAKER_01'))
            assert window.result['segments'][row]['speaker'] == 'SPEAKER_01'
        timed('undo', lambda: window.move_review_history(-1))
        timed('redo', lambda: window.move_review_history(1))
        assert window.result['segments'][600]['speaker'] == 'SPEAKER_01'
        status['checks'].append('turn assignment, undo and redo')

    def merge_identities():
        question = workspace_panel.QMessageBox.question
        workspace_panel.QMessageBox.question = staticmethod(lambda *args, **kwargs: workspace_panel.QMessageBox.Yes)
        try:
            dialog = workspace_panel.SpeakerDialog(window)
            dialog.speaker.setCurrentIndex(dialog.speaker.findData('SPEAKER_02'))
            dialog.target.setCurrentIndex(dialog.target.findData('SPEAKER_00'))
            timed('merge speaker identities', dialog.merge)
            dialog.deleteLater()
        finally:
            workspace_panel.QMessageBox.question = question
        assert all(s.get('speaker') != 'SPEAKER_02' for s in window.result['segments'])
        status['checks'].append('identity merge')

    def save_and_reopen():
        timed('save', lambda: window.save_edits())
        saved = load_review(folder)[0]
        assert not any(w.get('speaker') == 'SPEAKER_02' for w in saved['word_segments'])
        assert [speaker_name(s.get('speaker'), saved['speaker_names']) for s in saved['segments']] == names()
        status['checks'].append('saved review keeps turns and recognition word lists consistent')

    steps.extend([open_project, inline_edits, dialog_edits, merge_identities, save_and_reopen])

    def finish(ok, error='', waited=0):
        if scanner is not None and scanner.poll() is None and waited < 600:
            # The client calls into this thread, so keep serving events until it exits.
            QTimer.singleShot(200, lambda: finish(ok, error, waited + 1))
            return
        if scanner is not None:
            if scanner.poll() is None:
                scanner.kill()
                scanner.wait()
            try:
                status['uia_exit_code'] = scanner.returncode
                status['uia'] = json.loads(scan_output.read_text(encoding='utf-8-sig'))
                if ok and (scanner.returncode != 0 or status['uia'].get('scans', 0) < 1):
                    ok, error = False, 'The UI Automation client did not complete a scan of the window.'
            except (OSError, ValueError) as exc:
                ok, error = False, error or f'UI Automation client failed: {exc}'
        status['ok'] = ok
        if error:
            status['error'] = error
        report.write_text(json.dumps(status, indent=2), encoding='utf-8')
        application.exit(0 if ok else 1)

    def advance():
        try:
            read_accessible_table()
            if not steps:
                finish(True)
                return
            steps.pop(0)()
            QTimer.singleShot(150, advance)
        except Exception as exc:
            status['traceback'] = traceback.format_exc()
            finish(False, str(exc) or type(exc).__name__)

    def start():
        nonlocal scanner
        QAccessible.setActive(True)
        if os.name == 'nt':
            script = report.with_name(report.stem + '-uia.ps1')
            script.write_text(UIA_SCAN, encoding='utf-8')
            scanner = subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                                        '-ProcessId', str(os.getpid()), '-Seconds', '20', '-Output', str(scan_output)],
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            QTimer.singleShot(4000, advance)  # Let the client attach before the edits begin.
            return
        advance()

    QTimer.singleShot(300, start)
