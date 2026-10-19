"""Import an earlier queue or preservation copy after a preview."""
from pathlib import Path
import sys

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from transcript_desk.preservation import previous_work, merge_previous_work, create_backup
from transcript_desk.runtime import data_root
from transcript_desk.theme import apply_theme


class PreviousWorkDialog(QDialog):
    def __init__(self, parent, source):
        super().__init__(parent)
        self.setWindowTitle('Import previous work')
        self.resize(800, 450)
        self.source = Path(source)
        layout = QVBoxLayout(self)
        title = QLabel('Earlier application-data folder: ' + str(self.source))
        title.setWordWrap(True)
        layout.addWidget(title)
        explanation = QLabel('Saved recordings, edits, speaker names, annotations and snapshots remain in their recording folders. '
                             'The import adds their queue entries. Existing entries and project files are preserved.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        row = QHBoxLayout()
        self.projects_root = QLineEdit()
        self.projects_root.setReadOnly(True)
        self.projects_root.setPlaceholderText('Optional: locate moved recording folders')
        row.addWidget(self.projects_root, 1)
        browse = QPushButton('Locate recordings…')
        browse.clicked.connect(self.locate)
        row.addWidget(browse)
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Recording', 'Status', 'Saved folder'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.preferences = QCheckBox('Restore saved preferences, replacing the current values for those preferences')
        layout.addWidget(self.preferences)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Import')
        self.import_button = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def locate(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose the folder containing the earlier recording folders')
        if folder:
            self.projects_root.setText(folder)
            self.refresh()

    def refresh(self):
        try:
            self.jobs, self.saved_preferences, missing = previous_work(self.source, self.projects_root.text() or None)
            current = {str(Path(job['config']['output']).resolve()) for job in self.parent().store.jobs}
            duplicate = sum(str(Path(job['config']['output']).resolve()) in current for job in self.jobs)
            self.table.setRowCount(len(self.jobs))
            for row, job in enumerate(self.jobs):
                for column, value in enumerate((job.get('title') or job['source'], job['status'], job['config']['output'])):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.summary.setText(f'{len(self.jobs)} queue entries; {duplicate} existing entries will be skipped. '
                                 f'{len(missing)} recording folders are unavailable. Locate moved folders above or reconnect them later with Open project.')
            self.preferences.setEnabled(bool(self.saved_preferences))
            if not self.saved_preferences:
                self.preferences.setText('No preference copy was found. Settings from an earlier installation on this computer remain available.')
            self.import_button.setEnabled(True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.jobs, self.saved_preferences = [], {}
            self.summary.setText('Cannot import: ' + str(exc))
            self.import_button.setEnabled(False)


def apply_previous_work(window, jobs, preferences=None):
    """Apply an already previewed import while retaining the current loaded review."""
    window.finish_edit()
    if window.dirty and not window.save_edits():
        return None
    added, skipped = merge_previous_work(window.store, jobs)
    device_adapted = bool(preferences and preferences.get('device') == 'cuda' and sys.platform == 'darwin')
    if preferences:
        preferences = dict(preferences)
        if device_adapted:
            preferences['device'] = 'cpu'
        for key, value in preferences.items():
            window.settings.setValue(key, value)
        window.settings.sync()
        window.theme = window.settings.value('theme', window.theme)
        apply_theme(QApplication.instance(), window.theme)
        window.theme_button.setText('Light mode' if window.theme == 'dark' else 'Dark mode')
        window.device = window.settings.value('device', window.device)
        window.output.setText(window.settings.value('output', window.output.text()))
        window.refresh_engine_status()
    selected = window.selected()
    with QSignalBlocker(window.queue):
        window.refresh_queue()
        if selected in window.store.jobs:
            window.queue.selectRow(window.store.jobs.index(selected))
    window.update_controls()
    window.message.setText(f'Imported {added} earlier queue entries; skipped {skipped} existing entries. Saved project files were preserved.' +
                           (' The restored processing default uses CPU on this Mac; earlier queued configurations were retained.' if device_adapted else ''))
    return added, skipped


def import_previous_work(window):
    if window.active is not None or window.running:
        window.show_error('Pause processing before importing earlier work.')
        return
    source = QFileDialog.getExistingDirectory(window, 'Choose an earlier application-data folder or preservation backup')
    if not source:
        return
    dialog = PreviousWorkDialog(window, source)
    if dialog.exec() != QDialog.Accepted:
        return
    try:
        jobs, preferences, _ = previous_work(source, dialog.projects_root.text() or None)
        apply_previous_work(window, jobs, preferences if dialog.preferences.isChecked() else None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        window.show_error('Could not import previous work: ' + str(exc))


def backup_work(window):
    if window.active is not None or window.running:
        window.show_error('Pause processing before backing up work.')
        return
    window.finish_edit()
    if window.dirty and not window.save_edits():
        return
    destination = QFileDialog.getExistingDirectory(window, 'Choose a folder for a new work backup')
    if not destination:
        return
    try:
        if not window.review_writer.flush():
            raise OSError('A review save is still running. Try again after it finishes.')
        window.complete_review_saves()
        if window.dirty:
            raise OSError('The current review has unsaved changes. Save it before backing up work.')
        backup = create_backup(data_root(), window.settings, destination_parent=destination)
        if hasattr(window, 'preservation_warning'):
            window.statusBar().removeWidget(window.preservation_warning)
            window.preservation_warning.hide()
        window.message.setText('Work backup saved: ' + str(backup) +
                               '. Restore with Import previous work. Media and processing runtimes are excluded.')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        window.show_error('Could not back up work: ' + str(exc))
