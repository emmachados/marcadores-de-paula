"""Verify Setup acceptance in a real Qt process using an isolated credential backend."""
import json
from pathlib import Path
import sys
import traceback

import keyring
from keyring.backend import KeyringBackend
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from transcript_desk.setup_dialog import SetupDialog


class MemoryCredentials(KeyringBackend):
    priority = 1

    def __init__(self):
        self.values = {}

    def get_password(self, service, user):
        return self.values.get((service, user))

    def set_password(self, service, user, password):
        self.values[service, user] = password

    def delete_password(self, service, user):
        self.values.pop((service, user), None)


def run_setup_save_check(application, window, report_path):
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    credentials = MemoryCredentials()
    keyring.set_keyring(credentials)
    modes = iter([False, True, False])
    checks = []
    finished = False

    def finish(success, error=''):
        nonlocal finished
        if finished:
            return
        finished = True
        report.write_text(json.dumps({'success': success, 'error': error, 'checks': checks,
                                      'credential_backend': 'in-memory test double'}, indent=2), encoding='utf-8')
        if success:
            window.grab().save(str(report.with_suffix('.png')))
        application.exit(0 if success else 1)

    def exception(kind, value, tb):
        finish(False, ''.join(traceback.format_exception(kind, value, tb)))

    sys.excepthook = exception

    def next_mode():
        if finished:
            return
        try:
            remember = next(modes)
        except StopIteration:
            finish(True)
            return

        def click_save():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, SetupDialog)
            assert callable(dialog.done)
            dialog.token.setText('setup-verification-placeholder')
            dialog.remember.setChecked(remember)
            dialog.device.setCurrentIndex(dialog.device.findData('cuda' if remember else 'cpu'))
            dialog.save_button.click()

        QTimer.singleShot(100, click_save)
        window.setup_button.click()
        if finished:
            return
        assert window.isVisible()
        assert window.remember_token == remember
        assert window.settings.value('remember_token', type=bool) == remember
        assert window.device == ('cuda' if remember else 'cpu')
        assert bool(credentials.values) == remember

        def check_disposal():
            assert not window.findChildren(SetupDialog)
            checks.append({'remember': remember, 'main_window_visible': True, 'settings_saved': True,
                           'dialog_disposed': True})
            next_mode()

        QTimer.singleShot(100, check_disposal)

    QTimer.singleShot(15000, lambda: finish(False, 'Setup save check exceeded 15 seconds.'))
    QTimer.singleShot(0, next_mode)
