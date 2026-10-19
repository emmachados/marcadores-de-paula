"""Run the packaged Setup dialog against a fresh, isolated CPU environment."""
import json
import os
import stat
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QProcess, QTimer

from transcript_desk.runtime import engine_dir, engine_ready, runtime_root
from transcript_desk.setup_dialog import SetupDialog


def run_setup_check(application, window, report_path):
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    dialog = SetupDialog(window)
    window.setup_check_dialog = dialog
    device = sys.argv[sys.argv.index('--engine-device') + 1] if '--engine-device' in sys.argv else 'cpu'
    if device not in ('cpu', 'cuda'):
        raise ValueError('Unknown verification engine')
    dialog.device.setCurrentIndex(dialog.device.findData(device))
    dialog.show()
    finished = False
    simulate_link_error = "--simulate-python-link-error" in sys.argv
    injected = False
    minor_link = runtime_root() / "python/cpython-3.11-windows-x86_64-none"

    if simulate_link_error:
        normal_finished = dialog.process_finished

        def simulate_failure(code, status):
            nonlocal injected
            if dialog.step_number == 1 and code == 0 and status == QProcess.NormalExit:
                # The check removes only the junction created inside the temporary folder.
                if not minor_link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise RuntimeError("Expected a Windows junction in the isolated test directory.")
                os.rmdir(minor_link)
                dialog.read_output()
                message = "Failed to create Python minor version link directory (os error 448)"
                dialog.step_output += message
                dialog.write_log("Injected test failure: " + message)
                injected = True
                normal_finished(2, QProcess.NormalExit)
            else:
                normal_finished(code, status)

        dialog.process_finished = simulate_failure

    def finish(success, error=""):
        nonlocal finished
        if finished:
            return
        if success and simulate_link_error and (not injected or not dialog.recovered_python or os.path.lexists(minor_link)):
            success, error = False, "Recovery must complete with the minor-version junction absent."
        finished = True
        dialog.grab().save(str(report.with_suffix(".png")))
        report.write_text(json.dumps({"success": success, "error": error,
            "runtime_root": str(runtime_root()), "engine": str(engine_dir(device)),
            "device": device,
            "engine_record": json.loads((engine_dir(device) / 'ready.json').read_text()) if success else None,
            "log": str(dialog.log_path), "steps": dialog.step_number,
            "injected_link_error": injected, "recovered_python": str(dialog.recovered_python or ""),
            "minor_link_exists": os.path.lexists(minor_link),
            "text": dialog.log.toPlainText()}, indent=2), encoding="utf-8")
        application.exit(0 if success else 1)

    def exception(kind, value, tb):
        finish(False, "".join(traceback.format_exception(kind, value, tb)))

    sys.excepthook = exception
    if engine_ready(device):
        QTimer.singleShot(0, lambda: finish(False, "Setup verification requires a fresh engine directory."))
        return

    def poll():
        if engine_ready(device):
            finish(True)
        elif dialog.action and not dialog.commands and dialog.process is None:
            finish(False, dialog.step_status.text())

    dialog.check_timer = QTimer(dialog)
    dialog.check_timer.timeout.connect(poll)
    dialog.check_timer.start(1000)
    QTimer.singleShot(20 * 60 * 1000, lambda: finish(False, "Setup verification exceeded 20 minutes."))
    QTimer.singleShot(0, dialog.install_engine)
