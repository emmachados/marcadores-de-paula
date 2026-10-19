"""Reading, search, speaker management and portable project commands."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QTextCursor
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from transcript_desk.exports import export_result, speaker_name
from transcript_desk.text_tools import find_matches, reading_view, replace_matches, speaker_ids, speaker_statistics
from transcript_desk import turn_edits
from transcript_desk.project_archive import list_snapshots, read_archive, save_snapshot, write_archive


def commit_review(window, revised, row=0):
    if window.review_read_only:
        window.show_error('This project is open read-only in this application instance.')
        return False
    from transcript_desk.annotations import validate_annotations
    validate_annotations(revised)
    window.finish_edit()
    window.result = revised
    window.review_changed()
    window.refresh_review(row)
    return True


def run_dialog(dialog):
    try:
        return dialog.exec()
    finally:
        dialog.deleteLater()


def fill_speakers(combo, result, include_all=True):
    selected = combo.currentData() if combo.count() else '__all__'
    combo.blockSignals(True)
    combo.clear()
    if include_all:
        combo.addItem('All speakers', '__all__')
    identities = speaker_ids(result or {})
    labels = [speaker_name(identity, (result or {}).get('speaker_names', {})) for identity in identities]
    for identity, text in zip(identities, labels):
        # Earlier versions could create two identities with one name; show which is which.
        combo.addItem(f'{text} ({identity})' if identity and labels.count(text) > 1 else text, identity)
    index = combo.findData(selected)
    combo.setCurrentIndex(max(0, index))
    combo.blockSignals(False)


class ReadingPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.result, self.spans = window, None, []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 12)
        row = QHBoxLayout()
        self.speaker = QComboBox()
        self.speaker.setAccessibleName('Speaker reading filter')
        self.grouped = QCheckBox('Group by speaker')
        self.grouped.setChecked(True)
        self.times = QCheckBox('Show timestamps')
        self.paragraphs = QCheckBox('Keep turn paragraphs')
        for widget in (self.speaker, self.grouped, self.times, self.paragraphs):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName('Continuous speaker text')
        layout.addWidget(self.text, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        for name, callback in (('Copy text', self.copy_text), ('Go to source turn', self.go_to_source),
                               ('Export this view…', self.export_view), ('Manage speakers…', lambda: window.workspace.manage_speakers())):
            button = QPushButton(name)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.speaker.currentIndexChanged.connect(self.render)
        for checkbox in (self.grouped, self.times, self.paragraphs):
            checkbox.toggled.connect(self.render)

    def bind(self, result):
        self.result = result
        fill_speakers(self.speaker, result)
        self.setEnabled(result is not None)
        self.render()

    def options(self):
        selected = self.speaker.currentData()
        return {'speakers': None if selected == '__all__' else [selected],
                'timestamps': self.times.isChecked(), 'grouped': self.grouped.isChecked(),
                'paragraphs': self.paragraphs.isChecked()}

    def render(self, *_):
        if not self.result:
            self.text.clear()
            self.summary.clear()
            return
        selected = self.speaker.currentData()
        text, self.spans = reading_view(self.result, speaker=selected, all_speakers=selected == '__all__',
                                       timestamps=self.times.isChecked(), grouped=self.grouped.isChecked(),
                                       paragraphs=self.paragraphs.isChecked())
        self.text.setPlainText(text)
        summary = speaker_statistics(self.result)
        if selected != '__all__':
            summary = [row for row in summary if row['speaker'] == selected]
        self.summary.setText(f"{sum(row['turns'] for row in summary)} source turns · {sum(row['words'] for row in summary)} words. "
                             'Select a passage and use Go to source turn to edit or listen.')

    def copy_text(self):
        QApplication.clipboard().setText(self.text.textCursor().selectedText().replace('\u2029', '\n') or self.text.toPlainText())

    def go_to_source(self):
        cursor = self.text.textCursor()
        raw = self.text.toPlainText().encode('utf-16-le')
        position = len(raw[:cursor.position() * 2].decode('utf-16-le'))
        span = next((s for s in self.spans if s['start'] <= position <= s['end']), None)
        if span:
            self.window.tabs.setCurrentIndex(0)
            self.window.transcript.setCurrentCell(span['row'], 3)
            self.window.transcript.scrollToItem(self.window.transcript.item(span['row'], 3))
            self.window.player.player.setPosition(round(span['source_start'] * 1000))

    def export_view(self):
        directory = QFileDialog.getExistingDirectory(self, 'Export reading view')
        if directory:
            self.window.workspace.export_to(directory, ['TXT', 'DOCX'], self.options())


class SearchDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.matches = window, []
        self.setWindowTitle('Find and replace')
        self.resize(840, 580)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.query, self.replacement = QLineEdit(), QLineEdit()
        self.query.setAccessibleName('Find text')
        self.replacement.setAccessibleName('Replacement text')
        form.addRow('Find', self.query)
        form.addRow('Replace with', self.replacement)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.case, self.word = QCheckBox('Case sensitive'), QCheckBox('Whole words')
        self.speaker = QComboBox()
        fill_speakers(self.speaker, window.result)
        for widget in (self.case, self.word, self.speaker):
            row.addWidget(widget)
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setRange(0, 9999999)
            spin.setSuffix(' s')
        self.end.setValue(max((s['end'] for s in window.result['segments']), default=0))
        row.addWidget(QLabel('From'))
        row.addWidget(self.start)
        row.addWidget(QLabel('To'))
        row.addWidget(self.end)
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Turn', 'Speaker', 'Match in context'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.message = QLabel('Type text to preview every occurrence before replacing.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        for label, callback in (('Previous', lambda: self.step(-1)), ('Next', lambda: self.step(1)),
                                ('Listen', self.listen), ('Replace selected', self.replace_selected),
                                ('Replace all matches', self.replace_all)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.query.textChanged.connect(self.search)
        self.case.toggled.connect(self.search)
        self.word.toggled.connect(self.search)
        self.speaker.currentIndexChanged.connect(self.search)
        self.start.valueChanged.connect(self.search)
        self.end.valueChanged.connect(self.search)
        self.query.setFocus()

    def search(self, *_):
        selected = self.speaker.currentData()
        self.matches = find_matches(self.window.result, self.query.text(), case_sensitive=self.case.isChecked(),
                                    whole_word=self.word.isChecked(), speaker=selected, all_speakers=selected == '__all__',
                                    start=self.start.value(), end=self.end.value())
        self.table.setRowCount(len(self.matches))
        for index, match in enumerate(self.matches):
            segment = self.window.result['segments'][match['row']]
            for column, text in enumerate((str(match['row'] + 1), speaker_name(segment.get('speaker'), self.window.result.get('speaker_names', {})), match['context'])):
                self.table.setItem(index, column, QTableWidgetItem(text))
        self.message.setText(f'{len(self.matches)} matches. Replacement is literal and can be undone as one change.')
        if self.matches:
            self.table.selectRow(0)

    def step(self, direction):
        if self.matches:
            self.table.selectRow((self.table.currentRow() + direction) % len(self.matches))

    def listen(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.matches):
            segment = self.window.result['segments'][self.matches[row]['row']]
            self.window.workspace.audio.play_range(segment['start'], segment['end'])

    def replace_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        self.apply([self.matches[row] for row in rows])

    def replace_all(self):
        self.apply(self.matches)

    def apply(self, matches):
        if self.window.review_read_only:
            self.message.setText('Search is available; replacement is disabled for a read-only project.')
            return
        if not matches:
            return
        try:
            count = len(matches)
            commit_review(self.window, replace_matches(self.window.result, matches, self.replacement.text()), matches[0]['row'])
            self.search()
            self.message.setText(f'Replaced {count} occurrences. Affected annotations require review; Undo restores the batch.')
        except (ValueError, IndexError) as exc:
            self.message.setText(str(exc))


class SpeakerDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Manage speakers')
        self.resize(600, 500)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.speaker, self.target = QComboBox(), QComboBox()
        fill_speakers(self.speaker, window.result, False)
        fill_speakers(self.target, window.result, False)
        self.name, self.color, self.notes = QLineEdit(), QLineEdit(), QPlainTextEdit()
        self.color.setPlaceholderText('#4769B0')
        form.addRow('Speaker', self.speaker)
        form.addRow('Display name', self.name)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color)
        choose = QPushButton('Choose…')
        choose.clicked.connect(self.choose_color)
        color_row.addWidget(choose)
        form.addRow('Colour', color_row)
        form.addRow('Notes', self.notes)
        layout.addLayout(form)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        save = QPushButton('Save speaker details')
        save.clicked.connect(self.save_details)
        layout.addWidget(save)
        row = QHBoxLayout()
        row.addWidget(QLabel('Assign / merge into'))
        row.addWidget(self.target, 1)
        layout.addLayout(row)
        self.selected_rows = sorted({index.row() for index in window.transcript.selectedIndexes()})
        assign = QPushButton(f'Assign {len(self.selected_rows)} selected transcript turns')
        assign.setEnabled(bool(self.selected_rows))
        assign.clicked.connect(self.assign)
        merge = QPushButton('Merge selected speaker into target…')
        merge.clicked.connect(self.merge)
        layout.addWidget(assign)
        layout.addWidget(merge)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.speaker.currentIndexChanged.connect(self.populate)
        self.populate()

    def populate(self):
        identity = self.speaker.currentData()
        profile = self.window.result.get('speaker_profiles', {}).get(identity or '__unassigned__', {})
        self.name.setText(speaker_name(identity, self.window.result.get('speaker_names', {})))
        self.color.setText(profile.get('color', '#4769B0'))
        self.notes.setPlainText(profile.get('note', ''))
        stats = next((s for s in speaker_statistics(self.window.result) if s['speaker'] == identity), None)
        self.summary.setText(f"{stats['turns']} turns · {stats['words']} words · {stats['seconds']:.1f} summed turn seconds" if stats else '')

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.color.text()), self)
        if color.isValid():
            self.color.setText(color.name())

    def save_details(self):
        identity = self.speaker.currentData()
        if identity is None:
            self.message.setText('Assign unassigned turns to a named speaker in Edit turn before renaming.')
            return
        if not self.name.text().strip() or not QColor(self.color.text()).isValid():
            self.message.setText('Enter a speaker name and a valid colour.')
            return
        revised = copy.deepcopy(self.window.result)
        revised.setdefault('speaker_names', {})[identity] = self.name.text().strip()
        revised.setdefault('speaker_profiles', {})[identity] = {'color': QColor(self.color.text()).name(), 'note': self.notes.toPlainText()}
        commit_review(self.window, revised)
        fill_speakers(self.speaker, revised, False)
        fill_speakers(self.target, revised, False)
        self.message.setText('Speaker details saved in the review.')

    def assign(self):
        revised = copy.deepcopy(self.window.result)
        for row in self.selected_rows:
            turn_edits.assign_speaker(revised, row, self.target.currentData())
        commit_review(self.window, revised, self.selected_rows[0])
        self.populate()
        self.message.setText(f'Assigned {len(self.selected_rows)} turns. Undo restores the batch.')

    def merge(self):
        source, target = self.speaker.currentData(), self.target.currentData()
        if source == target:
            self.message.setText('Choose two different speaker identities.')
            return
        rows = [i for i, s in enumerate(self.window.result['segments']) if s.get('speaker') == source]
        if QMessageBox.question(self, 'Merge speaker identities',
                f"Assign all {len(rows)} turns by {self.speaker.currentText()} to {self.target.currentText()}? This is one undoable change.") != QMessageBox.Yes:
            return
        revised = copy.deepcopy(self.window.result)
        for row in rows:
            turn_edits.assign_speaker(revised, row, target)
        commit_review(self.window, revised, rows[0] if rows else 0)
        fill_speakers(self.speaker, revised, False)
        fill_speakers(self.target, revised, False)
        self.populate()


class WorkspaceController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.reading = ReadingPanel(window)
        window.tabs.insertTab(2, self.reading, 'Speaker text')
        from transcript_desk.audio_tools import AudioTools
        self.audio = AudioTools(window)
        window.player.layout().addWidget(self.audio)
        if hasattr(window.annotations, 'playRange'):
            window.annotations.playRange.connect(self.audio.play_range)
        window.tabs.currentChanged.connect(self.refresh)
        self.actions = []
        menu = window.menuBar().addMenu('Review')
        for name, callback, shortcut in (
                ('Find and replace…', self.search, 'Ctrl+F'),
                ('Manage speakers…', self.manage_speakers, ''),
                ('Continuous speaker text', lambda: window.tabs.setCurrentWidget(self.reading), ''),
                ('Add bookmark…', self.add_bookmark, 'Ctrl+B'),
                ('Bookmarks…', self.bookmarks, ''),
                ('Compare with machine transcript…', self.compare_original, ''),
                ('Save named snapshot…', self.save_named_snapshot, ''),
                ('Restore snapshot…', self.restore_snapshot, ''),
                ('Export options…', self.export_dialog, 'Ctrl+Shift+E'),
                ('Create portable project…', self.archive, '')):
            action = menu.addAction(name, callback)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            self.actions.append(action)
        project = window.menuBar().addMenu('Projects')
        project.addAction('Open project…', window.open_project)
        project.addAction('Import portable project…', self.import_archive)
        self.recent_menu = project.addMenu('Recent projects')
        self.recent_menu.aboutToShow.connect(self.show_recent)
        project.addAction('Export diagnostic logs…', self.export_diagnostics)
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(3000)
        self.position_timer.timeout.connect(self.remember_position)
        self.position_timer.start()
        self.restore_timer = QTimer(self)
        self.restore_timer.setSingleShot(True)
        self.restore_timer.timeout.connect(lambda: self.restore_position(self._restore_folder, self._restore_state))
        self.current_folder = None
        self.refresh()

    def refresh(self, *_):
        window = self.window
        for action in self.actions:
            mutates = action.text() in ('Manage speakers…', 'Add bookmark…', 'Save named snapshot…', 'Restore snapshot…')
            action.setEnabled(window.result is not None and not (mutates and window.review_read_only))
        if window.tabs.currentWidget() is self.reading:
            self.reading.bind(window.result)
        folder = window.result_job['config']['output'] if window.result_job and window.result else None
        if folder != self.current_folder:
            self.current_folder = folder
            self.audio.reset()
            if folder:
                recent = self.recent_paths()
                window.settings.setValue('recent_projects', json.dumps([folder] + [p for p in recent if p != folder][:14]))
                key = hashlib.sha256(folder.encode()).hexdigest()[:16]
                try:
                    state = json.loads(window.settings.value('position/' + key, '{}'))
                    if not isinstance(state, dict):
                        state = {}
                    if type(state.get('row', 0)) is not int:
                        state['row'] = 0
                    if window.result['segments']:
                        window.transcript.setCurrentCell(max(0, min(state.get('row', 0), len(window.result['segments']) - 1)), 3)
                    self._restore_folder, self._restore_state = folder, state
                    self.restore_timer.start(150)
                except (ValueError, TypeError):
                    pass

    def restore_position(self, folder, state):
        if self.current_folder == folder:
            try:
                position = max(0, min(9_000_000_000_000, int(state.get('audio_ms', 0))))
            except (TypeError, ValueError, OverflowError):
                position = 0
            self.window.player.player.setPosition(position)

    def remember_position(self):
        if self.current_folder and self.window.result:
            key = hashlib.sha256(self.current_folder.encode()).hexdigest()[:16]
            self.window.settings.setValue('position/' + key, json.dumps({'row': max(0, self.window.transcript.currentRow()),
                                                                       'audio_ms': self.window.player.player.position()}))

    def recent_paths(self):
        try:
            value = json.loads(self.window.settings.value('recent_projects', '[]'))
            return [p for p in value if isinstance(p, str)] if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []

    def show_recent(self):
        self.recent_menu.clear()
        for path in self.recent_paths():
            self.recent_menu.addAction(path, lambda checked=False, path=path: self.window.open_project_folder(path))

    def search(self):
        self.window.finish_edit()
        if self.window.result:
            run_dialog(SearchDialog(self.window))

    def manage_speakers(self):
        self.window.finish_edit()
        if self.window.result and not self.window.review_read_only:
            run_dialog(SpeakerDialog(self.window))

    def add_bookmark(self):
        if self.window.review_read_only:
            return
        self.window.finish_edit()
        name, accepted = QInputDialog.getText(self.window, 'Add bookmark', 'Bookmark name')
        if accepted and name.strip():
            revised = copy.deepcopy(self.window.result)
            row = max(0, self.window.transcript.currentRow())
            if not revised['segments']:
                return
            revised.setdefault('bookmarks', []).append({'name': name.strip(), 'turn_id': revised['segments'][row].get('turn_id'),
                                                       'row': row, 'seconds': self.window.player.player.position() / 1000})
            commit_review(self.window, revised, row)

    def bookmarks(self):
        entries = self.window.result.get('bookmarks', [])
        if not entries:
            self.window.show_error('This project has no bookmarks. Use Review → Add bookmark.')
            return
        choice, accepted = QInputDialog.getItem(self.window, 'Bookmarks', 'Go to bookmark', [f"{i+1}. {b['name']}" for i, b in enumerate(entries)], editable=False)
        if accepted:
            index = int(choice.split('.')[0]) - 1
            bookmark = entries[index]
            row = next((i for i, s in enumerate(self.window.result['segments']) if s.get('turn_id') == bookmark.get('turn_id')), bookmark.get('row', 0))
            self.window.tabs.setCurrentIndex(0)
            if 0 <= row < len(self.window.result['segments']):
                self.window.transcript.setCurrentCell(row, 3)
                self.window.transcript.scrollToItem(self.window.transcript.item(row, 3))
            self.window.player.player.setPosition(round(bookmark['seconds'] * 1000))

    def compare_original(self):
        from transcript_desk.review_storage import read_review
        try:
            original = read_review(Path(self.current_folder) / 'result.json')
            before = [s['text'] + '\n' for s in original['segments']]
            after = [s['text'] + '\n' for s in self.window.result['segments']]
            diff = ''.join(difflib.unified_diff(before, after, fromfile='Machine transcript', tofile='Current review')) or 'The wording is unchanged.'
            dialog = QDialog(self.window)
            dialog.setWindowTitle('Compare transcript wording')
            dialog.resize(850, 600)
            layout = QVBoxLayout(dialog)
            text = QPlainTextEdit(diff)
            text.setReadOnly(True)
            layout.addWidget(text)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            run_dialog(dialog)
        except (OSError, ValueError, TypeError) as exc:
            self.window.show_error(str(exc))

    def save_named_snapshot(self):
        if self.window.review_read_only:
            return
        if not self.window.save_edits():
            return
        name, accepted = QInputDialog.getText(self.window, 'Save snapshot', 'Snapshot name')
        if accepted:
            try:
                save_snapshot(self.current_folder, self.window.result, name)
                self.window.review_hint.setText('Saved snapshot: ' + name)
            except (ValueError, OSError) as exc:
                self.window.show_error(str(exc))

    def restore_snapshot(self):
        if self.window.review_read_only:
            return
        from transcript_desk.review_storage import read_review
        entries = list_snapshots(self.current_folder)
        if not entries:
            self.window.show_error('No named snapshots have been saved for this project.')
            return
        titles = [f'{i+1}. {name} ({date[:19]})' for i, (date, name, _) in enumerate(entries)]
        choice, accepted = QInputDialog.getItem(self.window, 'Restore snapshot', 'Current changes are saved as a recovery snapshot first.', titles, editable=False)
        if accepted and self.window.save_edits():
            try:
                restored = read_review(entries[titles.index(choice)][2])
                save_snapshot(self.current_folder, self.window.result, 'Before restoring ' + choice)
                commit_review(self.window, restored)
            except (ValueError, OSError) as exc:
                self.window.show_error(str(exc))

    def archive(self):
        if not self.window.save_edits():
            return
        destination, _ = QFileDialog.getSaveFileName(self.window, 'Create portable project', 'recording.paula.zip', 'Paula project (*.paula.zip)')
        if destination:
            include_media = QMessageBox.question(self.window, 'Include media', 'Include the recording audio in this project archive?', QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
            try:
                write_archive(self.current_folder, self.window.result, destination, include_media)
                self.window.review_hint.setText('Project archive saved to ' + destination)
            except (OSError, ValueError) as exc:
                self.window.show_error(str(exc))

    def import_archive(self):
        source, _ = QFileDialog.getOpenFileName(self.window, 'Import portable project', '', 'Paula project (*.paula.zip *.zip)')
        if not source:
            return
        parent = QFileDialog.getExistingDirectory(self.window, 'Folder in which to create the imported project')
        if not parent:
            return
        name, accepted = QInputDialog.getText(self.window, 'Imported project folder', 'New folder name', text=Path(source).name.removesuffix('.paula.zip').removesuffix('.zip'))
        if accepted and name and Path(name).name == name:
            try:
                folder = read_archive(source, Path(parent) / name)
                self.window.open_project_folder(folder)
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                self.window.show_error(str(exc))

    def export_dialog(self):
        from transcript_desk.export_dialog import ExportDialog
        self.window.finish_edit()
        if self.window.result:
            run_dialog(ExportDialog(self.window))

    def export_to(self, directory, formats, options):
        from datetime import datetime
        if not self.window.save_edits():
            return None
        destination = Path(directory) / ('reviewed-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        try:
            if options.get('separate_speakers'):
                identities = options.get('speakers') or speaker_ids(self.window.result)
                for index, identity in enumerate(identities):
                    export_result(self.window.result, destination / f'speaker-{index+1}', formats,
                                  options={**options, 'speakers': [identity]})
            else:
                export_result(self.window.result, destination, formats, options=options)
            self.window.review_hint.setText('Exported to ' + str(destination))
            return destination
        except (OSError, ValueError) as exc:
            self.window.show_error('Could not export: ' + str(exc))
            return None

    def export_diagnostics(self):
        destination, _ = QFileDialog.getSaveFileName(self.window, 'Export diagnostic logs', 'paula-diagnostics.zip', 'ZIP archive (*.zip)')
        if destination:
            try:
                from transcript_desk.diagnostics import export_diagnostics
                export_diagnostics(destination)
                self.window.statusBar().showMessage('Diagnostic logs exported.')
            except (ImportError, OSError, ValueError) as exc:
                self.window.show_error('Could not export diagnostic logs: ' + str(exc))
