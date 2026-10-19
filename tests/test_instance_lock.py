import os
from pathlib import Path
import subprocess
import sys

from transcript_desk.instance_lock import ApplicationLock


def run_child(code, folder):
    return subprocess.run([sys.executable, '-c', code, str(folder)], capture_output=True,
                          text=True, timeout=20)


def test_process_conflict_then_reopen_and_independent_data_folder(tmp_path):
    first = ApplicationLock(tmp_path)
    assert first.acquire()
    code = ('import sys; from transcript_desk.instance_lock import ApplicationLock; '
            'lock = ApplicationLock(sys.argv[1]); '
            'print("acquired" if lock.acquire() else "blocked"); lock.release()')
    try:
        blocked = run_child(code, tmp_path)
        assert blocked.returncode == 0 and blocked.stdout.strip() == 'blocked', blocked.stderr
        other = tmp_path / 'independent-data'
        other.mkdir()
        independent = run_child(code, other)
        assert independent.returncode == 0 and independent.stdout.strip() == 'acquired', independent.stderr
    finally:
        first.release()
    reopened = run_child(code, tmp_path)
    assert reopened.returncode == 0 and reopened.stdout.strip() == 'acquired', reopened.stderr


def test_application_lock_recovers_after_abrupt_process_exit(tmp_path):
    code = ('import os, sys; from transcript_desk.instance_lock import ApplicationLock; '
            'lock = ApplicationLock(sys.argv[1]); assert lock.acquire(); os._exit(23)')
    child = run_child(code, tmp_path)
    assert child.returncode == 23, child.stderr
    recovered = ApplicationLock(tmp_path)
    assert recovered.acquire()
    recovered.release()


def test_gui_second_launch_exits_before_constructing_queue_owner(tmp_path):
    root = tmp_path / 'app-data'
    root.mkdir()
    (root / 'queue.json').write_text('untouched earlier queue', encoding='utf-8')
    first = ApplicationLock(root)
    assert first.acquire()
    code = '''import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['TRANSCRIPT_DESK_DATA'] = sys.argv[1]
from PySide6.QtWidgets import QMessageBox
from transcript_desk import window, app
messages = []
QMessageBox.information = lambda parent, title, message: messages.append(message)
def unexpected_window():
    raise AssertionError('Second queue owner was constructed')
window.MainWindow = unexpected_window
assert app.main() == 1
assert len(messages) == 1 and 'already open' in messages[0]
print('Second GUI owner rejected before queue load')
'''
    try:
        child = run_child(code, root)
        assert child.returncode == 0, child.stderr
        assert 'rejected before queue load' in child.stdout
        assert (root / 'queue.json').read_text(encoding='utf-8') == 'untouched earlier queue'
        assert not (root / 'work-state.json').exists()
    finally:
        first.release()
