"""Exercise installed queue controls across two application processes with a worker fixture."""
import json
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QTimer

from transcript_desk.runtime import data_root


FIXTURE = '''import json, pathlib, subprocess, sys, time
if sys.argv[1] == '--child':
    heartbeat = pathlib.Path(sys.argv[2])
    while True:
        heartbeat.write_text(str(time.time()))
        time.sleep(0.1)
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
folder = pathlib.Path(config['output'])
folder.mkdir(parents=True, exist_ok=True)
marker = folder / 'saved-stage'
if not marker.exists():
    marker.write_text('fixture stage completed', encoding='utf-8')
    child = subprocess.Popen([sys.executable, __file__, '--child', str(folder / 'child-heartbeat')])
    while not (folder / 'child-heartbeat').exists():
        time.sleep(0.05)
    print(json.dumps({'event': 'progress', 'stage': 'Fixture processing', 'percent': None}), flush=True)
    time.sleep(60)
print(json.dumps({'event': 'complete', 'title': 'Pause verification', 'output': str(folder)}), flush=True)
'''


def run_pause_check(application, window, report_path, phase, mode, python):
    import transcript_desk.window as module
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    state = data_root()
    script = state / 'pause-worker-fixture.py'
    script.write_text(FIXTURE, encoding='utf-8')
    module.worker_command = lambda config, *_: [python, str(script), str(config)]
    application.setQuitOnLastWindowClosed(False)
    finished = False
    stopping = False
    checking_child = False
    checks = {'phase': phase, 'mode': mode, 'worker': 'subprocess fixture with a child heartbeat'}
    timer = QTimer(window)

    def finish(success, error=''):
        nonlocal finished
        if finished:
            return
        finished = True
        timer.stop()
        report.write_text(json.dumps({'success': success, 'error': error, 'log': window.log.toPlainText(), **checks}, indent=2), encoding='utf-8')
        if window.active:
            window.stop_worker()
            window.process.waitForFinished(5000)
        application.exit(0 if success else 1)

    sys.excepthook = lambda kind, value, tb: finish(False, ''.join(traceback.format_exception(kind, value, tb)))

    def begin():
        if phase == 'pause':
            assert not window.store.jobs, 'The first phase requires unused test state.'
            window.output.setText(str(state / 'outputs'))
            window.want_transcript.setChecked(False)
            window.add_sources(['https://example.com/pause', 'https://example.com/pending'], 'url')
            window.queue.selectRow(0)
            window.start_button.click()
        else:
            assert [j['status'] for j in window.store.jobs] == ['paused', 'queued']
            window.queue.selectRow(0)
            assert window.resume_button.isEnabled()
            checks['paused_state_restored'] = True
            # Keep the second queued recording pending for the next batch run.
            window.resume_button.click()
            window.running = False
        timer.start(50)

    def poll():
        nonlocal stopping, checking_child
        if phase == 'pause':
            if not stopping and window.message.text() == 'Fixture processing':
                stopping = True
                checks['output'] = window.store.jobs[0]['config']['output']
                if mode == 'close':
                    window.close()
                else:
                    window.pause_button.click()
            if stopping and window.active is None and not checking_child:
                checking_child = True
                assert [j['status'] for j in window.store.jobs] == ['paused', 'queued']
                window.grab().save(str(report.with_suffix('.png')))
                heartbeat = Path(checks['output']) / 'child-heartbeat'
                # Wait past any buffered child write before checking it has stopped.
                def sample_child():
                    content = heartbeat.read_bytes()
                    def verify_child():
                        assert heartbeat.read_bytes() == content, 'The worker child is still running.'
                        checks['child_stopped'] = True
                        checks['queued_recording_preserved'] = True
                        finish(True)
                    QTimer.singleShot(600, verify_child)
                QTimer.singleShot(600, sample_child)
        elif window.active is None:
            assert [j['status'] for j in window.store.jobs] == ['complete', 'queued'], window.message.text()
            job = window.store.jobs[0]
            first_report = report.with_name(report.name.replace('resume-verification', 'pause-verification'))
            prior = json.loads(first_report.read_text(encoding='utf-8'))
            assert job['config']['output'] == prior['output']
            assert not job.get('previous_outputs')
            assert (Path(job['config']['output']) / 'saved-stage').read_text() == 'fixture stage completed'
            checks['same_output_folder'] = True
            checks['queued_recording_preserved'] = True
            window.grab().save(str(report.with_suffix('.png')))
            finish(True)

    timer.timeout.connect(poll)
    QTimer.singleShot(45000, lambda: finish(False, 'Pause/resume verification exceeded 45 seconds.'))
    QTimer.singleShot(0, begin)
