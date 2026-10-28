from __future__ import annotations

import copy
import json
from contextlib import contextmanager
import os
import subprocess
import signal
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QProcess, QRect, QSettings, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
                             QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
                             QVBoxLayout, QWidget)

from transcript_desk.projects import project_job, media_in_project
from transcript_desk.checkpoints import can_resume
from transcript_desk.turn_dialog import TurnDialog
from transcript_desk import turn_edits
from transcript_desk.speaker_assignment import review_reasons, needs_speaker_model
from transcript_desk.review_storage import load_review, save_checkpoint
from transcript_desk.review_history import snapshot as history_snapshot
from transcript_desk.review import ReviewPlayer, TranscriptDelegate, active_text
from transcript_desk.annotation_panel import AnnotationPanel
from transcript_desk.annotations import invalidate
from transcript_desk.model_pack import inspect_pack
from transcript_desk.theme import apply_theme, palette_tokens
from transcript_desk.exports import FORMATS, export_result, speaker_name, timestamp
from transcript_desk.runtime import (QueueStore, child_environment, data_root, default_output_folder,
                                     engine_ready, engine_start_error, resource_root, tool_path, worker_command, start_process)
from transcript_desk.setup_dialog import SetupDialog, label


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Marcadores de Paula")
        self.resize(1280, 880)
        self.setMinimumSize(960, 680)
        self.settings = QSettings()
        self.theme = self.settings.value("theme", "dark")
        apply_theme(QApplication.instance(), self.theme)
        self.device = self.settings.value("device", "cpu")
        self.remember_token = self.settings.value("remember_token", False, type=bool)
        self.token = ""
        if self.remember_token:
            try:
                import keyring
                self.token = keyring.get_password("TranscriptDesk", "huggingface") or ""
            except Exception:
                pass
        self.store = QueueStore()
        self.active = None
        self.process = None
        self.running = False
        self.cancelled = False
        self.pausing = False
        self.close_after_pause = False
        self.buffer = b""
        self.worker_output = ""
        self.processing_started = None
        self.stage_started = None
        self.processing_stage = ""
        self.processing_stage_id = 'worker_start'
        self.processing_timer = QTimer(self)
        self.processing_timer.setInterval(1000)
        self.processing_timer.timeout.connect(self.refresh_processing_time)
        self.result = None
        self.result_job = None
        self.dirty = False
        self.review_history = []
        self.history_index = -1
        from transcript_desk.review_writer import ReviewWriter
        self.review_writer = ReviewWriter(self)
        self.review_writer.ready.connect(self.complete_review_saves)
        self.review_epoch, self.edit_revision, self.save_serial = 0, 0, 0
        self.pending_review_saves = {}
        self.completed_review_saves = {}
        self.project_lock = None
        self.review_read_only = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(2000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.build_ui()
        from transcript_desk.workspace_panel import WorkspaceController
        self.workspace = WorkspaceController(self)
        from transcript_desk.preservation_ui import import_previous_work, backup_work
        for action in self.menuBar().actions():
            if action.text() == 'Projects' and action.menu():
                action.menu().addSeparator()
                action.menu().addAction('Import previous work…', lambda: import_previous_work(self))
                action.menu().addAction('Back up work…', lambda: backup_work(self))
                break
        self.refresh_queue()
        self.refresh_engine_status()
        if self.store.load_error:
            self.message.setText(self.store.load_error)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        command_bar = QWidget()
        command_bar.setObjectName("commandBar")
        command_bar.setAttribute(Qt.WA_StyledBackground, True)
        header = QHBoxLayout(command_bar)
        header.setContentsMargins(16, 8, 16, 8)
        title = label("Marcadores de Paula")
        title.setObjectName("title")
        header.addWidget(title, 1)
        self.engine_label = label("")
        self.engine_label.setObjectName("muted")
        self.theme_button = QPushButton("Light mode" if self.theme == "dark" else "Dark mode")
        self.theme_button.clicked.connect(self.toggle_theme)
        self.open_project_button = QPushButton("Open project…")
        self.open_project_button.clicked.connect(self.open_project)
        header.addWidget(self.open_project_button)
        header.addWidget(self.theme_button)
        self.setup_button = QPushButton("Setup")
        self.setup_button.clicked.connect(self.open_setup)
        header.addWidget(self.setup_button)
        layout.addWidget(command_bar)
        split = QSplitter(Qt.Horizontal)
        layout.addWidget(split, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(480)
        self.import_dialog = QDialog(self)
        self.import_dialog.setWindowTitle("Add recordings")
        self.import_dialog.resize(570, 760)
        import_layout = QVBoxLayout(self.import_dialog)
        import_layout.addWidget(scroll)
        done = QPushButton("Done")
        done.clicked.connect(self.import_dialog.accept)
        import_layout.addWidget(done)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 16, 0)
        left_layout.setSpacing(12)
        section = label("Add recordings")
        section.setObjectName("section")
        left_layout.addWidget(section)
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("Paste video links, one per line")
        self.urls.setAccessibleName("Video links, one per line")
        self.urls.setFixedHeight(110)
        left_layout.addWidget(self.urls)
        row = QHBoxLayout()
        self.add_links = QPushButton("Add links")
        self.add_links.clicked.connect(self.add_urls)
        self.add_files = QPushButton("Add files…")
        self.add_files.clicked.connect(self.browse_files)
        row.addWidget(self.add_links)
        row.addWidget(self.add_files)
        left_layout.addLayout(row)
        self.import_feedback = label("", True)
        left_layout.addWidget(self.import_feedback)
        left_layout.addWidget(label("Each added recording uses the choices below. Playlists must be supplied as individual video links.", True))
        outputs = QGroupBox("Collect and prepare")
        output_layout = QVBoxLayout(outputs)
        self.want_transcript = QCheckBox("Transcript draft for review")
        self.want_transcript.setChecked(True)
        self.want_audio = QCheckBox("Audio · lossless FLAC")
        self.want_audio.setChecked(True)
        self.want_video = QCheckBox("Video · best available quality")
        for widget in (self.want_transcript, self.want_audio, self.want_video):
            output_layout.addWidget(widget)
        self.format_button = QToolButton()
        self.format_button.setText("Preferred export formats…")
        self.format_button.setPopupMode(QToolButton.InstantPopup)
        self.format_menu = QMenu(self)
        self.format_actions = {}
        for fmt in FORMATS:
            action = QAction(fmt, self)
            action.setCheckable(True)
            action.setChecked(True)
            self.format_actions[fmt] = action
            self.format_menu.addAction(action)
        self.format_button.setMenu(self.format_menu)
        output_layout.addWidget(self.format_button)
        self.layout_button = QToolButton()
        self.layout_button.setText("Preferred text layout…")
        self.layout_button.setPopupMode(QToolButton.InstantPopup)
        self.layout_menu = QMenu(self)
        self.layout_actions = {}
        for key, title, checked in (("timestamps", "Timestamps in TXT / DOCX", True),
                                    ("grouped", "Group text by speaker", False),
                                    ("paragraphs", "Keep turn paragraphs", True)):
            action = QAction(title, self)
            action.setCheckable(True)
            action.setChecked(checked)
            self.layout_actions[key] = action
            self.layout_menu.addAction(action)
        self.layout_button.setMenu(self.layout_menu)
        output_layout.addWidget(self.layout_button)
        left_layout.addWidget(outputs)
        speech = QGroupBox("Transcription")
        form = QFormLayout(speech)
        form.setSpacing(10)
        self.model = QComboBox()
        self.model.addItem("large-v3-turbo · faster", "large-v3-turbo")
        self.model.addItem("large-v3 · full model", "large-v3")
        form.addRow("Model", self.model)
        self.import_model = QPushButton("Add experimental model…")
        self.import_model.clicked.connect(self.choose_model_pack)
        form.addRow("", self.import_model)
        self.language = QComboBox()
        for title, code in (("Detect automatically", ""), ("English", "en"), ("Spanish", "es"),
                            ("French", "fr"), ("German", "de"), ("Italian", "it"), ("Portuguese", "pt"),
                            ("Catalan", "ca"), ("Galician", "gl"), ("Basque", "eu"), ("Arabic", "ar"),
                            ("Chinese", "zh"), ("Dutch", "nl"), ("Japanese", "ja"), ("Korean", "ko"),
                            ("Polish", "pl"), ("Russian", "ru"), ("Turkish", "tr"), ("Ukrainian", "uk")):
            self.language.addItem(title, code)
        form.addRow("Language", self.language)
        self.model.currentIndexChanged.connect(self.model_changed)
        models = resource_root() / "models"
        bundled = next((d for d in sorted(models.iterdir()) if (d / "paula-model.json").is_file()), None) \
            if models.is_dir() else None
        stock_default = resource_root() / "stock-default"
        if bundled:
            folder, metadata = inspect_pack(bundled)
            self.model.addItem(metadata["name"] + " · Spanish", "custom")
            self.model.setItemData(self.model.count() - 1, str(folder), Qt.UserRole + 1)
            if not stock_default.exists():
                self.model.setCurrentIndex(self.model.count() - 1)
        self.diarize = QCheckBox("Identify speakers")
        self.diarize.setChecked(True)
        form.addRow(self.diarize)
        self.speakers = QSpinBox()
        self.speakers.setRange(0, 100)
        self.speakers.setSpecialValueText("Detect automatically")
        self.speakers.setToolTip("Enter the known number of speakers. Choosing 1 skips automatic speaker detection and assigns all recognised speech to one speaker.")
        form.addRow("Speakers", self.speakers)
        self.diarize.toggled.connect(self.speakers.setEnabled)
        self.want_transcript.toggled.connect(speech.setEnabled)
        self.want_transcript.toggled.connect(self.format_button.setEnabled)
        self.want_transcript.toggled.connect(self.layout_button.setEnabled)
        left_layout.addWidget(speech)
        left_layout.addWidget(label("No wording cleanup is applied. Automatic recognition can still omit fillers, repetitions and false starts. Listen and review before analysis.", True))
        left_layout.addWidget(label("Output folder"))
        self.output = QLineEdit(self.settings.value("output", str(default_output_folder())))
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Output folder")
        left_layout.addWidget(self.output)
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_output)
        left_layout.addWidget(choose)
        left_layout.addWidget(label("Speaker labels are estimates. Review speaker changes and overlapping speech before using the annotations.", True))
        left_layout.addStretch()
        scroll.setWidget(left)
        sidebar = QWidget()
        sidebar.setObjectName("surface")
        sidebar.setMinimumWidth(270)
        sidebar.setMaximumWidth(360)
        sidebar.setAttribute(Qt.WA_StyledBackground, True)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 16, 12, 12)
        side_layout.setSpacing(10)
        add = QPushButton("Add recordings…")
        add.clicked.connect(self.import_dialog.open)
        side_layout.addWidget(add)
        split.addWidget(sidebar)
        right = QWidget()
        right.setObjectName("document")
        right.setAttribute(Qt.WA_StyledBackground, True)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        top = QHBoxLayout()
        queue_title = label("Recordings")
        queue_title.setObjectName("section")
        top.addWidget(queue_title, 1)
        self.count = label("", True)
        top.addWidget(self.count)
        side_layout.addLayout(top)
        self.recording_search = QLineEdit()
        self.recording_search.setPlaceholderText("Find a recording")
        self.recording_search.setClearButtonEnabled(True)
        self.recording_search.setAccessibleName("Find a recording")
        self.recording_search.textChanged.connect(self.filter_recordings)
        side_layout.addWidget(self.recording_search)
        self.queue = QTableWidget(0, 3)
        self.queue.setObjectName("recordings")
        self.queue.setHorizontalHeaderLabels(["Recording", "Outputs", "Status"])
        self.queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue.setSelectionMode(QAbstractItemView.SingleSelection)
        self.queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue.setAlternatingRowColors(False)
        self.queue.setShowGrid(False)
        self.queue.setColumnHidden(1, True)
        self.queue.verticalHeader().setDefaultSectionSize(54)
        self.queue.horizontalHeader().hide()
        self.queue.verticalHeader().hide()
        self.queue.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.queue.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.queue.setMinimumHeight(140)
        self.queue.setWordWrap(True)
        self.queue.itemSelectionChanged.connect(self.selection_changed)
        side_layout.addWidget(self.queue, 1)
        actions = QHBoxLayout()
        self.start_button = QPushButton("Start queue")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_queue)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setToolTip("Stop this recording and keep completed stages. Resume repeats the unfinished stage.")
        self.pause_button.clicked.connect(self.pause_queue)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_current)
        self.retry_button = QPushButton("Retry from start")
        self.retry_button.clicked.connect(self.retry_selected)
        self.resume_button = QPushButton("Resume")
        self.resume_button.clicked.connect(self.resume_selected)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        for button in (self.start_button, self.pause_button):
            actions.addWidget(button)
        side_layout.addLayout(actions)
        more = QHBoxLayout()
        for button in (self.resume_button, self.cancel_button):
            more.addWidget(button)
        side_layout.addLayout(more)
        retry_actions = QHBoxLayout()
        for button in (self.retry_button, self.remove_button):
            retry_actions.addWidget(button)
        side_layout.addLayout(retry_actions)
        self.issue_button = QPushButton('Processing error details…')
        self.issue_button.clicked.connect(self.show_processing_issue)
        self.issue_button.setEnabled(False)
        side_layout.addWidget(self.issue_button)
        self.message = label("Add links or files to begin.", True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        side_layout.addWidget(self.message)
        self.processing_time = label("", True)
        self.processing_time.setWordWrap(True)
        self.processing_time.setToolTip("Time since this stage started. It does not show how much work remains.")
        side_layout.addWidget(self.processing_time)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        side_layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        review = QWidget()
        review.setObjectName("document")
        review.setAttribute(Qt.WA_StyledBackground, True)
        review_layout = QVBoxLayout(review)
        review_layout.setContentsMargins(24, 22, 24, 0)
        review_layout.setSpacing(12)
        self.review_hint = label("Select a completed recording to review its transcript.", True)
        document_header = QHBoxLayout()
        self.recording_title = label("No recording selected")
        self.recording_title.setObjectName("recordingTitle")
        document_header.addWidget(self.recording_title, 1)
        self.export_button = QPushButton("Export…")
        self.export_button.clicked.connect(self.export_review)
        document_header.addWidget(self.export_button)
        review_layout.addLayout(document_header)
        self.recording_summary = label("Add audio, video, or links to begin.", True)
        review_layout.addWidget(self.recording_summary)
        self.player = ReviewPlayer(self)
        self.player.positionChanged.connect(self.highlight_position)
        self.transcript = QTableWidget(0, 4)
        self.transcript.setObjectName("transcript")
        self.transcript.setAccessibleName("Transcript: time, speaker, and editable wording")
        self.transcript.setHorizontalHeaderLabels(["Start", "End", "Speaker", "Transcript"])
        self.transcript.verticalHeader().hide()
        self.transcript.horizontalHeader().hide()
        self.transcript.setWordWrap(True)
        self.transcript.setTextElideMode(Qt.ElideNone)
        self.transcript.setAlternatingRowColors(False)
        self.transcript.setShowGrid(False)
        self.transcript.setColumnHidden(1, True)
        self.delegate = TranscriptDelegate(self.transcript)
        self.transcript.setItemDelegate(self.delegate)
        self.delegate.draftChanged.connect(self.schedule_autosave)
        self.transcript.cellClicked.connect(self.seek_turn)
        for column in range(3):
            self.transcript.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.transcript.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.transcript.horizontalHeader().sectionResized.connect(lambda *_: QTimer.singleShot(0, self.resize_transcript_rows))
        self.transcript.itemChanged.connect(self.edit_segment)
        self.transcript.itemSelectionChanged.connect(self.update_review_controls)
        turn_tools = QHBoxLayout()
        self.edit_turn_button = QPushButton("Edit turn…")
        self.edit_turn_button.clicked.connect(self.edit_turn)
        self.undo_review_button = QPushButton("Undo")
        self.undo_review_button.clicked.connect(lambda: self.move_review_history(-1))
        self.redo_review_button = QPushButton("Redo")
        self.redo_review_button.clicked.connect(lambda: self.move_review_history(1))
        self.speaker_review_button = QPushButton("Check speakers")
        self.speaker_review_button.setEnabled(False)
        self.speaker_review_button.setToolTip("Jump to the next turn with an ambiguous or missing automatic speaker assignment.")
        self.speaker_review_button.clicked.connect(self.next_speaker_review)
        for button in (self.edit_turn_button, self.undo_review_button, self.redo_review_button):
            turn_tools.addWidget(button)
        turn_tools.addWidget(self.speaker_review_button)
        turn_tools.addStretch()
        review_layout.addLayout(turn_tools)
        review_layout.addWidget(self.transcript, 1)
        tools = QHBoxLayout()
        self.save_review = QPushButton("Save review")
        self.save_review.clicked.connect(self.save_edits)
        self.open_folder = QPushButton("Open output folder")
        self.open_folder.clicked.connect(self.open_output)
        self.locate_audio = QPushButton("Locate audio…")
        self.locate_audio.clicked.connect(self.locate_media)
        tools.addWidget(self.save_review)
        tools.addWidget(self.locate_audio)
        tools.addWidget(self.open_folder)
        tools.addStretch()
        review_layout.addLayout(tools)
        review_layout.addWidget(self.review_hint)
        self.tabs.addTab(review, "Transcript")
        self.annotations = AnnotationPanel(self)
        self.annotations.changed.connect(self.review_changed)
        self.annotations.seek.connect(self.listen_annotation)
        self.tabs.addTab(self.annotations, "Annotations")
        self.tabs.currentChanged.connect(self.annotation_tab_changed)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.tabs.addTab(self.details, "Source and settings")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.tabs.addTab(self.log, "Processing log")
        right_layout.addWidget(self.tabs, 1)
        right_layout.addWidget(self.player)
        split.addWidget(right)
        split.setSizes([290, 990])
        self.statusBar().addPermanentWidget(self.engine_label)
        self.statusBar().showMessage("F4  Play / pause     F3  Replay three seconds     Ctrl+S  Save review")
        for key, callback in (("F4", self.player.toggle), ("F3", self.player.back.click),
                              ("Ctrl+S", self.save_edits), ("Ctrl+O", self.import_dialog.open),
                              ("Ctrl+Shift+O", self.open_project),
                              ("Ctrl+Z", lambda: self.move_review_history(-1)),
                              ("Ctrl+Shift+Z", lambda: self.move_review_history(1))):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)
        self.update_controls()

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Open a saved recording folder")
        if folder:
            self.open_project_folder(folder)

    def open_project_folder(self, folder):
        self.finish_edit()
        if self.dirty and not self.save_edits():
            return False
        try:
            path = Path(folder).resolve()
            existing = next((j for j in self.store.jobs if Path(j['config']['output']).resolve() == path), None)
            if existing is self.active and existing is not None:
                raise ValueError('This recording is still processing.')
            job = existing or project_job(path)
            if not existing:
                self.store.jobs.append(job)
                try:
                    self.store.save()
                except OSError:
                    self.store.jobs.remove(job)
                    raise
            self.recording_search.clear()
            self.refresh_queue()
            # The explicit load below also handles an already selected project.
            # Do not load it a second time through the selection signal.
            with QSignalBlocker(self.queue):
                self.queue.selectRow(self.store.jobs.index(job))
            if not self.load_result(job):
                self.restore_review_selection()
                return False
            self.update_controls()
            return True
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.show_error('Could not open project: ' + str(exc))
            return False

    def update_review_controls(self):
        if not hasattr(self, 'edit_turn_button'):
            return
        row = self.transcript.currentRow()
        self.edit_turn_button.setEnabled(bool(self.result and not self.review_read_only and 0 <= row < len(self.result['segments'])))
        self.undo_review_button.setEnabled(not self.review_read_only and self.history_index > 0)
        self.redo_review_button.setEnabled(not self.review_read_only and 0 <= self.history_index < len(self.review_history) - 1)

    def review_changed(self):
        if not self.result or self.review_read_only:
            return
        snapshot = history_snapshot(self.result, self.review_history[self.history_index] if self.review_history else None)
        if not self.review_history or snapshot != self.review_history[self.history_index]:
            self.review_history = self.review_history[:self.history_index + 1] + [snapshot]
            self.review_history = self.review_history[-21:]
            self.history_index = len(self.review_history) - 1
        self.update_review_controls()
        self.schedule_autosave()
        if hasattr(self, 'workspace'):
            self.workspace.refresh()

    def move_review_history(self, direction):
        if self.review_read_only:
            return
        self.finish_edit()
        target = self.history_index + direction
        if not 0 <= target < len(self.review_history):
            return
        row = self.transcript.currentRow()
        self.history_index = target
        self.result = copy.deepcopy(self.review_history[target])
        self.refresh_review(row)
        self.schedule_autosave()

    def refresh_review(self, row=0):
        self.render_transcript()
        if self.result['segments']:
            self.transcript.setCurrentCell(min(max(0, row), len(self.result['segments']) - 1), 3)
        self.annotations.bind(self.result)
        segments = self.result['segments']
        speakers = {s.get('speaker') for s in segments if s.get('speaker')}
        duration = max((s['end'] for s in segments), default=0)
        self.recording_summary.setText(f"{int(duration)//60}:{int(duration)%60:02d}   |   {len(speakers)} speakers   |   {len(segments)} turns")
        self.highlight_position(self.player.player.position())
        self.update_review_controls()
        if hasattr(self, 'workspace'):
            self.workspace.refresh()

    def apply_turn_edit(self, operation, row, *values):
        self.finish_edit()
        if not self.result or self.review_read_only:
            return False
        revised = copy.deepcopy(self.result)
        try:
            {'speaker': turn_edits.assign_speaker, 'speaker_name': turn_edits.assign_speaker_name,
             'bounds': turn_edits.set_bounds,
             'split': turn_edits.split_turn, 'merge': turn_edits.merge_turn}[operation](revised, row, *values)
        except (ValueError, IndexError) as exc:
            self.show_error(str(exc))
            return False
        self.result = revised
        self.review_changed()
        self.refresh_review(row)
        return True

    def edit_turn(self):
        self.finish_edit()
        row = self.transcript.currentRow()
        if not self.result or row < 0:
            return
        dialog = TurnDialog(self.result, row, self.player.player.position() / 1000, self)
        while dialog.exec() == QDialog.Accepted:
            operation = dialog.operation.currentIndex()
            if operation == 0:
                speaker = dialog.speaker.currentData()
                if speaker == '__new__':
                    name = dialog.name.text().strip()
                    if not name:
                        dialog.help.setText('Enter a name for the new speaker.')
                        continue
                    success = self.apply_turn_edit('speaker_name', row, name)
                else:
                    success = self.apply_turn_edit('speaker', row, speaker)
            elif operation == 1:
                success = self.apply_turn_edit('bounds', row, dialog.start.value(), dialog.end.value())
            elif operation == 2:
                success = self.apply_turn_edit('split', row, dialog.character(), dialog.split_time.value())
            else:
                success = self.apply_turn_edit('merge', row)
            if success:
                break
            dialog.help.setText(self.message.text())
        dialog.deleteLater()

    def filter_recordings(self):
        query = self.recording_search.text().casefold().strip()
        for row in range(self.queue.rowCount()):
            item = self.queue.item(row, 0)
            self.queue.setRowHidden(row, bool(query and item and query not in item.text().casefold()))

    def selected(self):
        row = self.queue.currentRow()
        return self.store.jobs[row] if 0 <= row < len(self.store.jobs) else None

    def formats(self):
        return [fmt for fmt, action in self.format_actions.items() if action.isChecked()]

    def options(self):
        if not any((self.want_transcript.isChecked(), self.want_audio.isChecked(), self.want_video.isChecked())):
            raise ValueError("Choose at least one output: transcript, audio or video.")
        if self.want_transcript.isChecked() and not self.formats():
            raise ValueError("Choose at least one transcript export format.")
        custom = self.model.currentData() == "custom"
        if custom:
            inspect_pack(self.model.currentData(Qt.UserRole + 1))
        return {"output": self.output.text(), "model": self.model.currentData(),
                "custom_model_path": self.model.currentData(Qt.UserRole + 1) if custom else None,
                "language": "es" if custom else self.language.currentData(),
                "transcribe": self.want_transcript.isChecked(), "save_audio": self.want_audio.isChecked(),
                "save_video": self.want_video.isChecked(), "diarize": self.diarize.isChecked(),
                "speakers": self.speakers.value(), "formats": self.formats(), "device": self.device,
                "text_layout": {key: action.isChecked() for key, action in self.layout_actions.items()},
                "review_before_export": True}

    def choose_model_pack(self):
        directory = QFileDialog.getExistingDirectory(self, "Select an experimental model pack")
        if not directory:
            return
        try:
            folder, metadata = inspect_pack(directory)
            self.model.addItem(metadata["name"] + " · experimental", "custom")
            self.model.setItemData(self.model.count() - 1, str(folder), Qt.UserRole + 1)
            self.model.setCurrentIndex(self.model.count() - 1)
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def model_changed(self, *_):
        custom = self.model.currentData() == "custom"
        if custom:
            self.language.setCurrentIndex(self.language.findData("es"))
        self.language.setEnabled(not custom)

    def add_sources(self, sources, kind):
        try:
            options = self.options()
            existing = {(j["source"], j["status"]) for j in self.store.jobs}
            count = 0
            for source in dict.fromkeys(sources):
                if any((source, status) in existing for status in ("queued", "running")):
                    continue
                self.store.add(source, kind, options)
                count += 1
            self.refresh_queue()
            noun = "recording" if count == 1 else "recordings"
            self.message.setText(f"{count} {noun} added. Choices are saved separately for each recording.")
            self.import_feedback.setText(f"{count} {noun} added to the queue. Select Done when ready.")
            return True
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))
            return False

    def add_urls(self):
        urls = [line.strip() for line in self.urls.toPlainText().splitlines() if line.strip()]
        if not urls:
            self.show_error("Paste at least one video link, one per line.")
            return
        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                self.show_error(f"This is not a complete video link: {url}")
                return
        if self.add_sources(urls, "url"):
            self.urls.clear()

    def browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add recordings", "", "Media (*.mp4 *.mkv *.webm *.mov *.avi *.wav *.mp3 *.m4a *.flac *.ogg *.opus *.aac);;All files (*)")
        if paths:
            self.add_sources(paths, "file")

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output.text())
        if path:
            self.output.setText(path)
            self.settings.setValue("output", path)

    def open_setup(self):
        dialog = SetupDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def refresh_engine_status(self):
        self.engine_label.setText(f"{self.device.upper()} engine " + ("ready" if engine_ready(self.device) else "needs setup"))

    def refresh_queue(self):
        selected = self.selected()
        selected_id = selected["id"] if selected else None
        blocker = QSignalBlocker(self.queue)
        self.queue.setRowCount(len(self.store.jobs))
        for row, job in enumerate(self.store.jobs):
            outputs = ", ".join(name for key, name in (("transcribe", "Text"), ("save_audio", "Audio"), ("save_video", "Video")) if job["config"][key])
            title = job.get("title") or (Path(job["source"]).name if job["config"]["source_type"] == "file" else job["source"])
            for col, text in enumerate((title, outputs, job["status"].capitalize())):
                item = QTableWidgetItem(text)
                item.setToolTip(job.get("message", "") if col == 2 else job["source"])
                self.queue.setItem(row, col, item)
            if job["id"] == selected_id:
                self.queue.selectRow(row)
        blocker.unblock()
        completed = sum(j["status"] == "complete" for j in self.store.jobs)
        self.count.setText(f"{completed} / {len(self.store.jobs)} complete")
        self.filter_recordings()
        self.update_controls()

    def update_controls(self):
        self.start_button.setEnabled(not self.running and self.active is None and any(j["status"] == "queued" for j in self.store.jobs))
        self.pause_button.setEnabled(self.active is not None and not self.pausing)
        self.cancel_button.setEnabled(self.active is not None and not self.pausing)
        self.setup_button.setEnabled(self.active is None and not self.running)
        job = self.selected()
        self.issue_button.setEnabled(bool(self.processing_issues(job)))
        self.retry_button.setEnabled(bool(job and job is not self.active and job["status"] in ("failed", "cancelled", "interrupted", "paused")))
        self.resume_button.setEnabled(bool(job and self.active is None and not self.running and can_resume(job)))
        self.remove_button.setEnabled(bool(job and job is not self.active))
        self.save_review.setEnabled(bool(self.result and self.result_job and self.result_job is not self.active and not self.review_read_only))
        self.export_button.setEnabled(self.save_review.isEnabled())
        self.locate_audio.setEnabled(self.save_review.isEnabled())
        self.open_folder.setEnabled(bool(job and Path(job["config"]["output"]).exists()))
        self.update_review_controls()

    def start_queue(self, checked=False, preferred=None):
        self.finish_edit()
        if self.dirty and not self.save_edits():
            return
        ffmpeg = tool_path("ffmpeg")
        if not ffmpeg:
            self.show_error("FFmpeg is missing. Reinstall the desktop application, or install FFmpeg for a source checkout.")
            return
        pending = [j for j in self.store.jobs if j["status"] == "queued"]
        if any(j["config"]["transcribe"] for j in pending):
            if not engine_ready(self.device):
                self.show_error("Open Setup and install the transcription engine before starting this queue.")
                return
            problem = engine_start_error(self.device)
            if problem:
                self.show_error("The transcription engine is installed but cannot start. Open Setup and install the "
                                "engine again; downloaded models are kept.\n\n" + problem)
                return
        self.running = True
        self.next_job(preferred)

    def next_job(self, preferred=None):
        if not self.running or self.active:
            return
        job = preferred if preferred and preferred['status'] == 'queued' else next(
            (j for j in self.store.jobs if j["status"] == "queued"), None)
        if not job:
            self.running = False
            self.message.setText("Queue finished. Select a recording to review its outputs.")
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.update_controls()
            return
        self.active = job
        self.cancelled = False
        self.pausing = False
        self.buffer = b""
        self.worker_output = ""
        job.update(status="running", message="Starting")
        job.pop('processing_issues', None)
        job.pop('warnings', None)
        config = job["config"]
        config.update(device=self.device, ffmpeg=tool_path("ffmpeg"), deno=tool_path("deno"))
        directory = data_root() / "jobs"
        try:
            directory.mkdir(exist_ok=True)
            config_path = directory / f"{job['id']}.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            self.store.save()
        except OSError as exc:
            job.update(status="failed", message=str(exc))
            self.active = None
            self.running = False
            self.show_error(str(exc))
            self.refresh_queue()
            return
        command = worker_command(config_path, self.device, config["transcribe"])
        self.log.appendPlainText(f"Starting: {job['source']}")
        self.process = QProcess(self)
        self.process.setProcessEnvironment(child_environment(self.token))
        # Windows file dialogs can move the window's working folder to the user's media.
        self.process.setWorkingDirectory(str(directory))
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.job_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.progress.setRange(0, 0)
        self.message.setText("Starting recording…")
        self.processing_started = self.stage_started = monotonic()
        self.processing_stage = "Starting recording"
        self.processing_stage_id = 'worker_start'
        self.processing_timer.start()
        self.refresh_processing_time()
        self.refresh_queue()
        if self.result_job is job:
            self.load_result(job)
        try:
            start_process(self.process, command)
        except OSError as exc:
            self.log.appendPlainText(str(exc))
            self.process_error(QProcess.FailedToStart)

    def refresh_processing_time(self):
        if self.processing_started is None:
            return
        now = monotonic()
        def clock(seconds):
            minutes, seconds = divmod(max(0, int(seconds)), 60)
            return f"{minutes}:{seconds:02d}"
        self.processing_time.setText(
            f"Elapsed {clock(now - self.processing_started)} · Current stage {clock(now - self.stage_started)} · "
            + ("NVIDIA" if self.device == "cuda" else "CPU"))

    def read_stdout(self):
        if self.process is None:
            return
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not self.active or not isinstance(event, dict):
                continue
            kind = event.get("event")
            required = {'progress': ('stage',), 'timing': ('stage', 'status'),
                        'complete': ('title',), 'error': ('message',), 'warning': ('message',)}
            if any(not isinstance(event.get(key), str) for key in required.get(kind, ())):
                continue
            if kind in ('progress', 'timing'):
                from transcript_desk.processing_errors import STAGE_LABELS
                if event.get('stage_id') in STAGE_LABELS:
                    self.processing_stage_id = event['stage_id']
            if kind == "progress":
                if self.processing_stage != event["stage"]:
                    self.processing_stage = event["stage"]
                    self.stage_started = monotonic()
                self.refresh_processing_time()
                self.active["message"] = event["stage"]
                self.message.setText(event["stage"])
                pct = event.get("percent")
                if not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
                    pct = None
                self.progress.setRange(0, 100 if pct is not None else 0)
                if pct is not None:
                    self.progress.setValue(max(0, min(100, int(pct))))
            elif kind == "timing":
                if isinstance(event.get('seconds'), (int, float)):
                    self.log.appendPlainText(f"{event['stage']}: {event['seconds']:.1f} s ({event['status']})")
            elif kind == "complete":
                for issue in event.get('issues', []) if isinstance(event.get('issues'), list) else []:
                    self.add_processing_issue(self.active, issue)
                if isinstance(event.get('warnings'), list):
                    self.active['warnings'] = list(dict.fromkeys(
                        self.active.get('warnings', []) + [w for w in event['warnings'] if isinstance(w, str)]))
                self.active.update(status="complete", title=event["title"], message=self.completion_message(self.active))
            elif kind == "error":
                self.add_processing_issue(self.active, event.get('issue'))
                self.active.update(status="failed", message=event["message"], partial=event.get("partial", False))
                self.log.appendPlainText(event["message"])
            elif kind == "warning":
                self.add_processing_issue(self.active, event.get('issue'))
                self.active.setdefault("warnings", []).append(event["message"])
                self.log.appendPlainText(event["message"])

    def read_stderr(self):
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if self.token:
            text = text.replace(self.token, "[redacted]")
        # Kept for the saved issue; the log tab is lost when the window closes.
        self.worker_output = (self.worker_output + text)[-8000:]
        self.log.appendPlainText(text.rstrip())

    def process_error(self, error):
        if error == QProcess.FailedToStart and self.active:
            from transcript_desk.processing_errors import process_exit_issue, issue_text
            issue = process_exit_issue(-1, stage='worker_start', failed_to_start=True)
            self.add_processing_issue(self.active, issue)
            self.active.update(status="failed", message=issue_text(issue))
            self.job_finished(-1, QProcess.CrashExit)

    def job_finished(self, code, status):
        if not self.active:
            return
        self.read_stdout()
        self.read_stderr()
        job = self.active
        if job["status"] == "complete":
            job['message'] = self.completion_message(job)
        elif self.pausing:
            job.update(status="paused", message="Paused. Resume keeps completed stages and repeats the unfinished stage.")
        elif self.cancelled:
            job.update(status="cancelled", message="Cancelled. Completed media files and any partial transcript are kept.")
        elif code != 0 or status != QProcess.NormalExit:
            if job["status"] != "failed":
                self.record_process_exit(job, code, status == QProcess.CrashExit)
        elif job["status"] != "complete":
            self.record_process_exit(job, code, False)
        saved = self.save_queue()
        if not saved:
            self.running = False
            self.close_after_pause = False
        self.refresh_processing_time()
        self.processing_timer.stop()
        self.processing_started = None
        self.active = None
        self.process.deleteLater()
        self.process = None
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if job["status"] == "complete" else 0)
        if saved:
            self.message.setText(job["message"])
        self.refresh_queue()
        if self.selected() is job and not self.dirty:
            self.load_result(self.selected())
        if self.running:
            QTimer.singleShot(100, self.next_job)
        self.update_controls()
        if self.close_after_pause:
            self.close_after_pause = False
            QTimer.singleShot(0, self.close)

    @staticmethod
    def completion_message(job):
        return 'Complete with warnings. Open Processing error details or the processing log.' if (
            job.get('warnings') or job.get('processing_issues')) else 'Complete'

    def add_processing_issue(self, job, issue):
        if not isinstance(issue, dict) or any(not isinstance(issue.get(key), str) for key in (
                'stage', 'category', 'title', 'summary', 'action', 'details')):
            return
        issues = job.setdefault('processing_issues', [])
        if not isinstance(issues, list):
            issues = job['processing_issues'] = []
        if issue not in issues:
            issues.append(copy.deepcopy(issue))
            from transcript_desk.diagnostics import record_processing_issue
            record_processing_issue(issue)
        self.issue_button.setEnabled(bool(self.processing_issues(self.selected())))

    def record_process_exit(self, job, code, crashed):
        from transcript_desk.processing_errors import process_exit_issue, issue_text
        folder = Path(job['config']['output'])
        partial = any((folder / name).is_file() for name in ('result.json', 'partial.json'))
        issue = process_exit_issue(code, crashed=crashed, stage=self.processing_stage_id, partial=partial,
                                   output=self.worker_output)
        self.add_processing_issue(job, issue)
        job.update(status='failed', message=issue_text(issue), partial=partial)
        self.log.appendPlainText(issue_text(issue, include_details=True))

    def processing_issues(self, job):
        issues = job.get('processing_issues', []) if job else []
        issues = issues if isinstance(issues, list) else []
        if job is self.result_job and self.result:
            saved = self.result.get('metadata', {}).get('processing_issues', [])
            if isinstance(saved, list):
                issues = issues + saved
        valid = []
        for issue in issues:
            if isinstance(issue, dict) and all(isinstance(issue.get(key), str) for key in (
                    'stage', 'category', 'title', 'summary', 'action', 'details')) and issue not in valid:
                valid.append(issue)
        return valid

    def show_processing_issue(self):
        from transcript_desk.processing_issue_dialog import ProcessingIssueDialog
        issues = self.processing_issues(self.selected())
        if not issues:
            return
        for issue in issues:
            dialog = ProcessingIssueDialog(issue, self)
            dialog.exec()
            dialog.deleteLater()

    def pause_queue(self):
        self.running = False
        if self.pausing:
            return
        if self.active and self.process:
            self.pausing = True
            previous = {key: self.active[key] for key in ('status', 'message')}
            if self.active['status'] != 'complete':
                self.active.update(status="paused", message="Pausing; completed stages are retained.")
            try:
                self.store.save()
            except OSError as exc:
                self.pausing = False
                self.close_after_pause = False
                self.active.update(previous)
                self.show_error("Could not save the paused state: " + str(exc))
                return
            self.message.setText("Pausing the current recording…")
            if not self.stop_worker():
                self.pausing = False
                self.close_after_pause = False
                self.active.update(previous)
                self.save_queue()
        self.update_controls()

    def resume_selected(self):
        job = self.selected()
        if not job or self.active or not can_resume(job):
            return
        self.finish_edit()
        if self.dirty and not self.save_edits():
            return
        if job['config'].get('device') != self.device:
            self.show_error("Select the original processing engine in Setup before resuming, or use Retry from start.")
            return
        previous = dict(job)
        job.update(status="queued", message="Waiting to resume from the last completed stage")
        if not self.save_queue():
            job.clear()
            job.update(previous)
            return
        self.refresh_queue()
        self.start_queue(preferred=job)
        if job['status'] == 'queued' and not self.running:
            job.update(status='paused', message='Resume pending. Resolve the error shown below and try Resume again.')
            self.save_queue()
            self.refresh_queue()

    def stop_worker(self):
        if not self.process:
            return True
        pid = self.process.processId()
        if not pid:
            self.process.kill()
            return True
        if os.name == "nt":
            try:
                stopped = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                         capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                if stopped.returncode and self.process.state() != QProcess.NotRunning:
                    self.show_error('Windows could not stop the processing tree. The recording is still active; try Pause again.')
                    return False
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.show_error('Could not stop processing: ' + str(exc))
                return False
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                self.process.kill()
            except OSError as exc:
                self.show_error('Could not stop processing: ' + str(exc))
                return False
        return True

    def cancel_current(self):
        if not self.process:
            return
        self.cancelled = True
        self.running = False
        self.message.setText("Cancelling the current recording…")
        if not self.stop_worker():
            self.cancelled = False

    def retry_selected(self):
        self.finish_edit()
        job = self.selected()
        if job and job["status"] in ("failed", "cancelled", "interrupted", "paused"):
            if self.dirty and not self.save_edits():
                return
            previous = copy.deepcopy(job)
            # Preserve partial results and reviewer changes from the previous attempt.
            output = Path(job["config"]["output"])
            if output.exists():
                job.setdefault("previous_outputs", []).append(str(output))
                job["config"]["output"] = str(output.parent / (output.name + "-retry"))
            job.update(status="queued", message="Waiting to retry")
            if not self.save_queue():
                job.clear()
                job.update(previous)
                return
            self.refresh_queue()
            self.load_result(job)

    def remove_selected(self):
        self.finish_edit()
        job = self.selected()
        if job and job is not self.active:
            if self.dirty and not self.save_edits():
                return
            index = self.store.jobs.index(job)
            self.store.jobs.pop(index)
            if not self.save_queue():
                self.store.jobs.insert(index, job)
                return
            with QSignalBlocker(self.queue):
                self.queue.clearSelection()
            self.refresh_queue()
            self.load_result(None)
            self.message.setText("Removed from the queue. Output files remain in their folder.")

    def selection_changed(self):
        self.finish_edit()
        if self.dirty and not self.save_edits():
            self.restore_review_selection()
            return
        if not self.load_result(self.selected()):
            self.restore_review_selection()
        self.update_controls()

    def restore_review_selection(self):
        with QSignalBlocker(self.queue):
            if self.result_job in self.store.jobs:
                self.queue.selectRow(self.store.jobs.index(self.result_job))
            else:
                self.queue.clearSelection()

    def load_result(self, job):
        if hasattr(self, 'workspace'):
            self.workspace.remember_position()
        opened = self._load_result(job)
        if opened:
            self.review_epoch += 1
            self.edit_revision = 0
            self.configure_project_lock()
        if hasattr(self, 'workspace'):
            self.workspace.refresh()
        return opened

    def configure_project_lock(self):
        from transcript_desk.project_lock import ProjectLock
        folder = Path(self.result_job['config']['output']).resolve() if self.result and self.result_job else None
        if self.project_lock and self.project_lock.folder != folder:
            self.project_lock.release()
            self.project_lock = None
        self.review_read_only = False
        if folder:
            if self.project_lock is None:
                self.project_lock = ProjectLock(folder)
            self.review_read_only = not self.project_lock.acquire()
            if self.review_read_only:
                self.review_hint.setText('Read-only project. ' + self.project_lock.message)
        self.transcript.setEditTriggers(QAbstractItemView.NoEditTriggers if self.review_read_only else
                                        QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        if hasattr(self.annotations, 'set_read_only'):
            self.annotations.set_read_only(self.review_read_only)
        self.save_review.setEnabled(bool(self.result) and not self.review_read_only)
        self.update_review_controls()

    def _load_result(self, job):
        loaded = (None, None, [])
        if job is not None and job is not self.active:
            try:
                loaded = load_review(Path(job['config']['output']))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.review_hint.setText('Could not open transcript: ' + str(exc))
                return False
        self.autosave_timer.stop()
        self.result, self.result_job, self.dirty = None, job, False
        self.review_history, self.history_index = [], -1
        self.update_review_controls()
        self.annotations.bind(None)
        self.delegate.active_row, self.delegate.span = -1, None
        self.player.load(None)
        with QSignalBlocker(self.transcript):
            self.transcript.setRowCount(0)
        self.details.clear()
        self.recording_title.setText(job.get("title", "Recording") if job else "No recording selected")
        self.recording_summary.setText("Add audio, video, or links to begin." if not job else job.get("status", "").capitalize())
        if not job:
            self.review_hint.setText("Select a completed recording to review its transcript.")
            return True
        self.details.setPlainText(json.dumps(job, ensure_ascii=False, indent=2))
        self.review_hint.setText(job.get("message", ""))
        if job is self.active:
            return True
        output = Path(job["config"]["output"])
        try:
            self.result, path, failures = loaded
            if self.result is None:
                return True
            self.result.setdefault("speaker_names", {})
            self.render_transcript()
            self.annotations.bind(self.result)
            self.review_history, self.history_index = [copy.deepcopy(self.result)], 0
            self.update_review_controls()
            metadata = self.result.get("metadata", {})
            self.recording_title.setText(metadata.get("title") or job.get("title", "Recording"))
            segments = self.result.get("segments", [])
            speakers = {s.get("speaker") for s in segments if s.get("speaker")}
            duration = max((s["end"] for s in segments), default=0)
            self.recording_summary.setText(f"{int(duration)//60}:{int(duration)%60:02d}   |   {len(speakers)} speakers   |   {len(segments)} turns")
            media = media_in_project(self.result, output)
            if media:
                metadata['review_media_path'] = str(media)
            self.review_history, self.history_index = [copy.deepcopy(self.result)], 0
            self.player.load(media, metadata.get("title", job.get("title", "Recording")))
            warning = "Partial result. " if job["status"] != "complete" else ""
            if path.name.startswith("autosave"):
                warning += "Recovered the latest valid autosave. "
            if failures:
                warning += "Unreadable review file(s) skipped: " + ", ".join(failures) + ". "
            self.review_hint.setText(warning + "Click a time to listen. Double-click text to edit it, or a speaker to reassign that turn. Changes save automatically.")
            self.details.setPlainText(json.dumps(self.result.get("metadata", {}), ensure_ascii=False, indent=2))
            return True
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.result = None
            self.review_hint.setText("Could not open transcript: " + str(exc))
            return False

    @contextmanager
    def bulk_transcript_update(self):
        # ResizeToContents re-measures every row after each changed cell, which
        # made one speaker edit take 20 s on a one-hour recording. Measure once.
        header = self.transcript.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        try:
            with QSignalBlocker(self.transcript):
                yield
        finally:
            for column in range(3):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

    def style_speaker_item(self, item, segment):
        reasons = review_reasons(segment)
        item.setToolTip("\n".join(reasons))
        item.setForeground(QBrush(QColor(palette_tokens(self.theme)["highlight" if reasons else "muted"])))

    def update_speaker_review_count(self):
        count = sum(bool(review_reasons(s)) for s in self.result.get("segments", []))
        self.speaker_review_button.setText(f"Check speakers ({count})" if count else "Check speakers")
        self.speaker_review_button.setMinimumWidth(
            self.speaker_review_button.fontMetrics().horizontalAdvance(self.speaker_review_button.text()) + 28)
        self.speaker_review_button.setEnabled(count > 0)

    def render_transcript(self):
        segments = self.result.get("segments", [])
        with self.bulk_transcript_update():
            self.transcript.setRowCount(len(segments))
            for row, segment in enumerate(segments):
                values = [timestamp(segment["start"], "."), timestamp(segment["end"], "."),
                          speaker_name(segment.get("speaker"), self.result["speaker_names"]), segment["text"]]
                for column, text in enumerate(values):
                    item = self.transcript.item(row, column)
                    if item is not None:
                        # Replacing items tears down their accessibility objects, the
                        # path of the diagnosed macOS crash. Update existing cells.
                        if item.text() != text:
                            item.setText(text)
                        if column < 3:
                            item.setForeground(QBrush(QColor(palette_tokens(self.theme)["muted"])))
                        if column == 2:
                            self.style_speaker_item(item, segment)
                        continue
                    item = QTableWidgetItem(text)
                    if column < 2:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if column < 3:
                        font = QFont(self.transcript.font())
                        font.setPixelSize(12)
                        font.setBold(column == 2)
                        item.setFont(font)
                        item.setForeground(QBrush(QColor(palette_tokens(self.theme)["muted"])))
                    if column == 2:
                        self.style_speaker_item(item, segment)
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                    self.transcript.setItem(row, column, item)
            self.resize_transcript_rows()
        self.update_speaker_review_count()

    def next_speaker_review(self):
        segments = (self.result or {}).get("segments", [])
        flagged = [i for i, segment in enumerate(segments) if review_reasons(segment)]
        if not flagged:
            return
        current = self.transcript.currentRow()
        row = next((i for i in flagged if i > current), flagged[0])
        self.transcript.setCurrentCell(row, 2)
        self.transcript.scrollToItem(self.transcript.item(row, 2))
        self.seek_turn(row, 0)
        self.review_hint.setText(" ".join(review_reasons(segments[row])))

    def resize_transcript_rows(self, rows=None):
        width = max(40, self.transcript.columnWidth(3) - 32)
        for row in range(self.transcript.rowCount()) if rows is None else rows:
            item = self.transcript.item(row, 3)
            if item:
                bounds = self.transcript.fontMetrics().boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, item.text())
                self.transcript.setRowHeight(row, max(64, bounds.height() + 32))
        if self.delegate.editor is not None and self.delegate.editor_index.isValid():
            row = self.delegate.editor_index.row()
            self.transcript.setRowHeight(row, max(96, self.transcript.rowHeight(row)))

    def edit_segment(self, item):
        if not self.result or item.column() not in (2, 3) or not 0 <= item.row() < len(self.result['segments']):
            return
        row = item.row()
        segment = self.result["segments"][item.row()]
        if self.review_read_only:
            with QSignalBlocker(self.transcript):
                item.setText(segment['text'] if item.column() == 3 else speaker_name(segment.get('speaker'), self.result['speaker_names']))
            return
        if item.column() == 2:
            # A typed name reassigns this turn only; Manage speakers renames an identity.
            changed = turn_edits.assign_speaker_name(self.result, row, item.text())
            # Replacing items inside a delegate's setModelData callback destroys
            # objects that Qt is still using. Update labels after that callback.
            QTimer.singleShot(0, self.refresh_speaker_labels)
            if not changed:
                return
        elif item.column() == 3:
            segment["text"] = item.text()
            # Previous word alignment is no longer an alignment of the edited text.
            if "words" in segment:
                segment["original_words"] = segment.pop("words")
            segment["text_edited"] = True
            invalidate(self.result, item.row())
        self.resize_transcript_rows([row])
        self.highlight_position(self.player.player.position())
        self.review_changed()

    def finish_edit(self):
        self.delegate.finish()
        self.annotations.flush_note()

    def refresh_speaker_labels(self):
        if not self.result:
            return
        draft = self.delegate.draft()
        with self.bulk_transcript_update():
            for row, segment in enumerate(self.result['segments']):
                if draft is not None and draft[:2] == (row, 2):
                    continue
                item = self.transcript.item(row, 2)
                if item is not None:
                    value = speaker_name(segment.get('speaker'), self.result['speaker_names'])
                    if item.text() != value:
                        item.setText(value)
                    self.style_speaker_item(item, segment)
        self.update_speaker_review_count()

    def schedule_autosave(self):
        if not self.result or not self.result_job or self.review_read_only:
            return
        self.edit_revision += 1
        self.dirty = True
        self.review_hint.setText("Saving changes automatically…")
        if not self.autosave_timer.isActive():
            self.autosave_timer.start()

    def review_snapshot(self):
        result = copy.deepcopy(self.result)
        draft = self.delegate.draft()
        if draft is not None:
            row, column, value = draft
            segment = result["segments"][row]
            if column == 3 and segment["text"] != value:
                segment["text"] = value
                if "words" in segment:
                    segment["original_words"] = segment.pop("words")
                segment["text_edited"] = True
                invalidate(result, row)
            elif column == 2 and value != speaker_name(segment.get("speaker"), result["speaker_names"]):
                turn_edits.assign_speaker_name(result, row, value)
        return result

    def autosave(self):
        if self.dirty:
            self.persist_review(automatic=True)

    def persist_review(self, automatic=False):
        if not self.result or not self.result_job:
            return True
        if self.review_read_only:
            return not self.dirty
        try:
            if automatic and any(epoch == self.review_epoch for epoch, _ in self.pending_review_saves.values()):
                self.autosave_timer.start()
                return True
            if not automatic:
                if not self.review_writer.flush():
                    raise OSError('A previous save is still running. The current review remains open.')
                self.complete_review_saves()
            result = self.review_snapshot()
            now = datetime.now(timezone.utc)
            result.setdefault("metadata", {})["review_saved_at"] = now.isoformat()
            result["metadata"]["review_save_kind"] = "automatic" if automatic else "manual"
            self.save_serial += 1
            token = self.save_serial
            self.pending_review_saves[token] = (self.review_epoch, self.edit_revision)
            self.review_writer.submit(Path(self.result_job['config']['output']), result, automatic=automatic, token=token)
            if not automatic:
                if not self.review_writer.flush():
                    raise OSError('Saving is still running. The current review remains open.')
                self.complete_review_saves()
                if not self.completed_review_saves.pop(token, False):
                    return False
                self.result = result
                self.annotations.result = self.result
            return True
        except (ValueError, OSError, TypeError, IndexError, KeyError, RuntimeError) as exc:
            self.dirty = True
            self.review_hint.setText("Changes are NOT saved: " + str(exc) + ". Retrying automatically.")
            self.log.appendPlainText("Could not save the review: " + str(exc))
            self.autosave_timer.start()
            return False

    def complete_review_saves(self):
        for outcome in self.review_writer.take_outcomes():
            token = outcome['token']
            generation = self.pending_review_saves.pop(token, None)
            if not outcome['automatic']:
                self.completed_review_saves[token] = outcome['ok']
            if generation is None or generation[0] != self.review_epoch:
                continue
            if outcome['ok']:
                if generation[1] == self.edit_revision:
                    self.dirty = False
                    self.autosave_timer.stop()
                    self.review_hint.setText(('Autosaved. ' if outcome['automatic'] else 'Review saved. ') + 'The original transcript is retained.')
                if outcome['revision_error']:
                    self.review_hint.setText('Review saved; additional history revision unavailable: ' + outcome['revision_error'])
            else:
                self.dirty = True
                self.review_hint.setText('Changes are NOT saved: ' + outcome['error'] + '. Retrying automatically.')
                self.log.appendPlainText('Could not save the review: ' + outcome['error'])
            if self.dirty and not self.autosave_timer.isActive():
                self.autosave_timer.start()

    def save_edits(self):
        self.finish_edit()
        return self.persist_review()

    def annotation_tab_changed(self, *_):
        if self.tabs.currentWidget() is self.annotations:
            self.finish_edit()
            self.annotations.bind(self.result)

    def listen_annotation(self, seconds):
        if self.player.play.isEnabled():
            self.player.player.setPosition(round(seconds * 1000))
            self.player.player.play()

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.settings.setValue("theme", self.theme)
        apply_theme(QApplication.instance(), self.theme)
        self.theme_button.setText("Light mode" if self.theme == "dark" else "Dark mode")
        self.transcript.blockSignals(True)
        color = QBrush(QColor(palette_tokens(self.theme)["muted"]))
        for row in range(self.transcript.rowCount()):
            for column in range(3):
                self.transcript.item(row, column).setForeground(color)
        self.transcript.blockSignals(False)
        self.highlight_position(self.player.player.position())

    def seek_turn(self, row, column):
        if self.result and column == 0:
            self.player.player.setPosition(int(self.result["segments"][row]["start"] * 1000))

    def highlight_position(self, milliseconds):
        segments = self.result.get("segments", []) if self.result else []
        row, span = active_text(segments, milliseconds / 1000)
        previous = self.delegate.active_row
        self.delegate.active_row, self.delegate.span = row, span
        # Highlighting is a paint concern; changing model roles here caused Qt
        # to reload active editor contents while playback was advancing.
        self.transcript.viewport().update()
        if row >= 0 and row != previous and self.player.follow.isChecked() and self.transcript.state() != QAbstractItemView.EditingState:
            self.transcript.scrollToItem(self.transcript.item(row, 3), QAbstractItemView.EnsureVisible)

    def locate_media(self):
        path, _ = QFileDialog.getOpenFileName(self, "Locate the original recording")
        if path and self.result:
            self.workspace.audio.reset()
            self.result.setdefault("metadata", {})["review_media_path"] = path
            self.player.load(path, Path(path).name)
            self.review_changed()

    def export_review(self):
        if not self.result or not self.save_edits():
            return
        self.workspace.export_dialog()

    def open_output(self):
        job = self.selected()
        if job:
            QDesktopServices.openUrl(QUrl.fromLocalFile(job["config"]["output"]))

    def show_error(self, message):
        self.message.setText(message)
        self.import_feedback.setText(message)
        self.log.appendPlainText(message)

    def save_queue(self):
        try:
            self.store.save()
            return True
        except (ValueError, OSError, TypeError) as exc:
            self.show_error('Could not save the recording queue: ' + str(exc))
            return False

    def closeEvent(self, event):
        if self.active:
            self.close_after_pause = True
            self.pause_queue()
            event.ignore()
            return
        self.finish_edit()
        if self.dirty and not self.save_edits():
            event.ignore()
            return
        self.player.player.stop()
        if not self.save_queue():
            event.ignore()
            return
        if hasattr(self, 'workspace'):
            self.workspace.remember_position()
            self.workspace.restore_timer.stop()
            self.workspace.position_timer.stop()
            self.workspace.audio.shutdown()
        self.review_writer.shutdown()
        if self.project_lock:
            self.project_lock.release()
        event.accept()
