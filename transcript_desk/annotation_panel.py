"""Annotation review, configurable project vocabulary and source-linked passages."""
import copy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QItemSelectionModel
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                              QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QFormLayout, QDoubleSpinBox, QSplitter, QScrollArea,
                              QSizePolicy, QLineEdit, QDialog, QDialogButtonBox, QListWidget,
                              QListWidgetItem, QFileDialog, QAbstractItemView)
from transcript_desk.annotations import (SCHEMA, STATUSES, CERTAINTIES, create_annotation,
                                        create_time_annotation, create_range_annotation,
                                        duplicate_annotation, suggest, is_current, fingerprint,
                                        migrate_annotations, touch_annotation, export_annotations)
from transcript_desk.codebook import ensure_codebook, entries, resolve_entry, annotation_name
from transcript_desk.codebook_dialog import CodebookDialog
from transcript_desk.exports import timestamp, speaker_name


def writable(method):
    def invoke(self, *args, **kwargs):
        if self.read_only:
            self.message.setText('This project is open read-only in this application instance.')
            return
        if not method.__code__.co_flags & 4:
            args = args[:method.__code__.co_argcount - 1]
        return method(self, *args, **kwargs)
    return invoke


class AnnotationPanel(QWidget):
    changed = Signal()
    seek = Signal(float)  # Retained for older integrations.
    playRange = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.current_id = None
        self.loading = False
        self.read_only = False
        self.shortcuts = []
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.setInterval(300)
        self.note_timer.timeout.connect(self.changed.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 8)
        hint = QLabel("Label selected wording, complete turn ranges, or timed events. Categories and functions are defined in the project codebook.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        top = QHBoxLayout()
        self.turn = QComboBox()
        self.turn.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.turn.setMinimumContentsLength(12)
        self.turn.setAccessibleName("Annotation recording turn")
        self.turn.currentIndexChanged.connect(self.show_turn)
        top.addWidget(self.turn, 1)
        self.find_button = QPushButton("Find candidates")
        self.find_button.clicked.connect(self.find_candidates)
        top.addWidget(self.find_button)
        self.codebook_button = QPushButton("Codebook…")
        self.codebook_button.clicked.connect(self.edit_codebook)
        top.addWidget(self.codebook_button)
        layout.addLayout(top)
        self.passage = QPlainTextEdit()
        self.passage.setReadOnly(True)
        self.passage.setAccessibleName("Select transcript passage to annotate")
        self.passage.setMaximumHeight(100)
        layout.addWidget(self.passage)
        actions = QHBoxLayout()
        self.new_label = QComboBox()
        self.new_label.setAccessibleName("New annotation label")
        actions.addWidget(self.new_label, 1)
        self.add_button = QPushButton("Label selection")
        self.add_button.clicked.connect(self.add_selection)
        actions.addWidget(self.add_button)
        self.multilabel_button = QPushButton("Several labels…")
        self.multilabel_button.clicked.connect(self.choose_multiple_labels)
        actions.addWidget(self.multilabel_button)
        self.time_button = QPushButton("Timed event…")
        self.time_button.clicked.connect(self.add_time_event)
        actions.addWidget(self.time_button)
        self.listen = QPushButton("Listen")
        self.listen.clicked.connect(self.listen_turn)
        actions.addWidget(self.listen)
        layout.addLayout(actions)
        ranges = QHBoxLayout()
        ranges.addWidget(QLabel("Through turn"))
        self.end_turn = QComboBox()
        self.end_turn.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.end_turn.setMinimumContentsLength(12)
        self.end_turn.setAccessibleName("Final turn for a multi-turn annotation")
        ranges.addWidget(self.end_turn, 1)
        self.range_button = QPushButton("Label turn range")
        self.range_button.clicked.connect(self.add_turn_range)
        ranges.addWidget(self.range_button)
        self.reviewer = QLineEdit()
        self.reviewer.setPlaceholderText("Reviewer name (optional)")
        self.reviewer.setAccessibleName("Reviewer for new annotations")
        ranges.addWidget(self.reviewer, 1)
        layout.addLayout(ranges)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search passages and notes")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", None)
        for status in STATUSES:
            self.status_filter.addItem(status, status)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status_filter)
        self.speaker_filter = QComboBox()
        self.speaker_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.speaker_filter)
        layout.addLayout(filters)
        category_filters = QHBoxLayout()
        self.label_filter, self.function_filter = QComboBox(), QComboBox()
        for combo in (self.label_filter, self.function_filter):
            combo.currentIndexChanged.connect(self.refresh)
            category_filters.addWidget(combo, 1)
        self.from_time, self.until_time = QDoubleSpinBox(), QDoubleSpinBox()
        for title, spin in (("From", self.from_time), ("Until", self.until_time)):
            spin.setRange(-1, 9999999)
            spin.setDecimals(3)
            spin.setSpecialValueText("Any time")
            spin.setValue(-1)
            spin.setSuffix(" s")
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(title + " annotation time filter")
            spin.valueChanged.connect(self.refresh)
            category_filters.addWidget(QLabel(title))
            category_filters.addWidget(spin)
        layout.addLayout(category_filters)
        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Passage / event", "Label", "Function", "Status", "Speaker"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Interactive)
            self.table.setColumnWidth(column, 115)
        self.table.itemSelectionChanged.connect(self.select_annotation)
        split.addWidget(self.table)
        self.editor = QWidget()
        form = QFormLayout(self.editor)
        form.setFormAlignment(Qt.AlignTop)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.kind, self.function = QComboBox(), QComboBox()
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.certainty = QComboBox()
        self.certainty.addItems(CERTAINTIES)
        self.author = QLineEdit()
        self.author.setAccessibleName("Annotation author")
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(70)
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.note.setAccessibleName("Annotation note")
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setRange(0, 9999999)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setKeyboardTracking(False)
        self.timing = QLabel()
        self.timing.setWordWrap(True)
        self.dates = QLabel()
        self.dates.setWordWrap(True)
        for title, widget in (("Label", self.kind), ("Function", self.function), ("Status", self.status),
                              ("Certainty", self.certainty), ("Author", self.author), ("Start", self.start), ("End", self.end)):
            form.addRow(title, widget)
        form.addRow(self.timing)
        form.addRow("Note", self.note)
        form.addRow(self.dates)
        self.apply_categories_button = QPushButton("Apply categories to selected rows")
        self.apply_categories_button.clicked.connect(self.apply_categories)
        form.addRow(self.apply_categories_button)
        relations = QHBoxLayout()
        self.link_button, self.unlink_button = QPushButton("Link selected"), QPushButton("Remove links")
        self.link_button.clicked.connect(self.link_selected)
        self.unlink_button.clicked.connect(self.unlink_selected)
        relations.addWidget(self.link_button)
        relations.addWidget(self.unlink_button)
        form.addRow(relations)
        related_row = QHBoxLayout()
        self.related = QComboBox()
        self.related.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.related.setMinimumContentsLength(12)
        self.go_related = QPushButton("Go")
        self.go_related.clicked.connect(self.go_to_related)
        related_row.addWidget(self.related, 1)
        related_row.addWidget(self.go_related)
        form.addRow("Related", related_row)
        self.reattach = QPushButton("Reattach to selection")
        self.reattach.clicked.connect(self.reattach_selection)
        form.addRow(self.reattach)
        for combo in (self.kind, self.function, self.status, self.certainty):
            combo.setMinimumHeight(28)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
            combo.currentIndexChanged.connect(self.update_annotation)
        self.author.textEdited.connect(self.author_changed)
        self.note.textChanged.connect(self.note_changed)
        self.start.valueChanged.connect(self.update_timing)
        self.end.valueChanged.connect(self.update_timing)
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        scroll.setMinimumWidth(245)
        split.addWidget(scroll)
        split.setSizes([650, 280])
        layout.addWidget(split, 1)
        batch = QHBoxLayout()
        for text, callback in (("Confirm selected", lambda: self.batch_status("confirmed")),
                               ("Reject selected", lambda: self.batch_status("rejected")),
                               ("Next unresolved", self.next_unresolved), ("Duplicate", self.duplicate_selected),
                               ("Delete", self.delete_selected), ("Export filtered…", self.export_filtered)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            batch.addWidget(button)
        layout.addLayout(batch)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.bind(None)

    def flush_note(self):
        if self.note_timer.isActive():
            self.note_timer.stop()
            self.changed.emit()

    def set_read_only(self, value):
        self.read_only = bool(value)
        for button in self.findChildren(QPushButton):
            if button.text() not in ('Listen', 'Next unresolved', 'Export filtered…', 'Go'):
                button.setEnabled(not self.read_only)
        for field in (self.new_label, self.reviewer, self.kind, self.function, self.status,
                      self.certainty, self.author, self.note, self.start, self.end):
            field.setEnabled(not self.read_only)

    @staticmethod
    def fill_categories(combo, categories, all_text=None):
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        if all_text:
            combo.addItem(all_text, None)
        for entry in categories:
            pixmap = QPixmap(12, 12)
            pixmap.fill(QColor(entry["color"]))
            combo.addItem(QIcon(pixmap), entry["name"], entry["id"])
            detail = entry["definition"]
            if entry.get("group"):
                detail = entry["group"] + ": " + detail
            combo.setItemData(combo.count() - 1, detail, Qt.ToolTipRole)
        found = combo.findData(selected)
        combo.setCurrentIndex(found if found >= 0 else 0)
        combo.blockSignals(False)

    def populate_categories(self):
        active_labels = entries(self.result or {}, "labels", active_only=True)
        self.fill_categories(self.new_label, active_labels)
        self.fill_categories(self.kind, entries(self.result or {}, "labels"))
        self.fill_categories(self.function, entries(self.result or {}, "functions"))
        self.fill_categories(self.label_filter, entries(self.result or {}, "labels"), "All labels")
        self.fill_categories(self.function_filter, entries(self.result or {}, "functions"), "All functions")
        for shortcut in self.shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.shortcuts = []
        for kind in ("labels", "functions"):
            for entry in entries(self.result or {}, kind, active_only=True):
                if entry["shortcut"]:
                    shortcut = QShortcut(QKeySequence(entry["shortcut"]), self)
                    shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                    if kind == "labels":
                        shortcut.activated.connect(lambda identity=entry["id"]: self.apply_labels([identity]))
                    else:
                        shortcut.activated.connect(lambda identity=entry["id"]: self.apply_function(identity))
                    self.shortcuts.append(shortcut)

    def bind(self, result):
        self.flush_note()
        self.loading = True
        self.result = result
        self.current_id = None
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        if result is not None:
            ensure_codebook(result)
            migrate_annotations(result)
        self.populate_categories()
        for combo in (self.turn, self.end_turn, self.speaker_filter):
            combo.blockSignals(True)
            combo.clear()
        self.end_turn.addItem("Same turn", None)
        self.speaker_filter.addItem("All speakers", None)
        names = (result or {}).get("speaker_names", {})
        speakers = set()
        for i, segment in enumerate((result or {}).get("segments", [])):
            name = speaker_name(segment.get("speaker"), names)
            text = f"{i + 1} · {timestamp(segment['start'], '.')} · {name}"
            self.turn.addItem(text, i)
            self.end_turn.addItem(text, i)
            speaker = segment.get("speaker") or "__unassigned__"
            if speaker not in speakers:
                speakers.add(speaker)
                self.speaker_filter.addItem(name, speaker)
        for combo in (self.turn, self.end_turn, self.speaker_filter):
            combo.blockSignals(False)
        self.setEnabled(bool(result and result.get("segments")))
        self.loading = False
        self.show_turn()
        self.refresh()

    def annotation_speakers(self, a):
        segments = self.result.get("segments", [])
        identities = {span["turn_id"] for span in a.get("spans", [])}
        selected = [s for s in segments if s.get("turn_id") in identities] if identities else [segments[a["segment"]]]
        return {s.get("speaker") or "__unassigned__" for s in selected}

    def filtered_annotations(self):
        if not self.result:
            return []
        query = self.search.text().strip().casefold()
        annotations = []
        for a in self.result.get("annotations", []):
            if query and query not in (a["quote"] + " " + a["note"] + " " + a.get("author", "")).casefold():
                continue
            if self.status_filter.currentData() and a["status"] != self.status_filter.currentData():
                continue
            if self.label_filter.currentData() and a["label_id"] != self.label_filter.currentData():
                continue
            if self.function_filter.currentData() and a["function_id"] != self.function_filter.currentData():
                continue
            if self.speaker_filter.currentData() and self.speaker_filter.currentData() not in self.annotation_speakers(a):
                continue
            if self.from_time.value() >= 0 and a["end"] < self.from_time.value():
                continue
            if self.until_time.value() >= 0 and a["start"] > self.until_time.value():
                continue
            annotations.append(a)
        return annotations

    def selected_ids(self):
        return {self.table.item(index.row(), 0).data(Qt.UserRole) for index in self.table.selectionModel().selectedRows()}

    def refresh(self, *_):
        if self.loading:
            return
        selected = self.selected_ids()
        if self.current_id:
            selected.add(self.current_id)
        annotations = self.filtered_annotations()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(annotations))
        names = (self.result or {}).get("speaker_names", {})
        for row, a in enumerate(annotations):
            speakers = ", ".join(speaker_name(None if s == "__unassigned__" else s, names) for s in sorted(self.annotation_speakers(a)))
            passage = a["quote"] or f"Timed event: {timestamp(a['start'], '.')} – {timestamp(a['end'], '.')}"
            for col, text in enumerate((passage, annotation_name(self.result, a), annotation_name(self.result, a, "functions"), a["status"], speakers)):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, a["id"])
                if col == 1:
                    entry = resolve_entry(self.result, "labels", a["label_id"])
                    swatch = QPixmap(10, 10)
                    swatch.fill(QColor(entry["color"]))
                    item.setIcon(QIcon(swatch))
                    item.setToolTip(entry["definition"])
                self.table.setItem(row, col, item)
                if a["id"] in selected:
                    item.setSelected(True)
            if a["id"] == self.current_id:
                self.table.setCurrentCell(row, 0, QItemSelectionModel.NoUpdate)
        self.table.blockSignals(False)
        if self.current_id not in {a["id"] for a in annotations}:
            self.current_id = None
        self.editor.setEnabled(self.annotation() is not None)
        self.message.setText(f"{len(annotations)} of {len((self.result or {}).get('annotations', []))} annotations shown. "
                             "Select several rows for batch review. Changes are undoable.")
        self.set_read_only(self.read_only)

    def annotation(self):
        return next((a for a in (self.result or {}).get("annotations", []) if a["id"] == self.current_id), None)

    def show_turn(self, *_):
        row = self.turn.currentData()
        self.passage.setPlainText(self.result["segments"][row]["text"] if self.result and row is not None else "")

    def selection(self):
        cursor = self.passage.textCursor()
        raw = self.passage.toPlainText().encode("utf-16-le")
        start = len(raw[:cursor.selectionStart() * 2].decode("utf-16-le", errors="ignore"))
        end = len(raw[:cursor.selectionEnd() * 2].decode("utf-16-le", errors="ignore"))
        return start, end

    @writable
    def add_records(self, records):
        if not records:
            return
        self.flush_note()
        self.result.setdefault("annotations", []).extend(records)
        self.result["annotation_schema"] = SCHEMA
        self.current_id = records[-1]["id"]
        self.refresh()
        self.select_id(self.current_id)
        self.changed.emit()

    @writable
    def apply_labels(self, identities):
        if not self.result:
            return
        start, end = self.selection()
        if start == end:
            self.message.setText("Select the wording to annotate first.")
            return
        try:
            records = [create_annotation(self.result, self.turn.currentData(), start, end, identity, author=self.reviewer.text().strip()) for identity in identities]
        except (ValueError, IndexError) as exc:
            self.message.setText(str(exc))
            return
        self.add_records(records)

    def add_selection(self):
        self.apply_labels([self.new_label.currentData()])

    @writable
    def choose_multiple_labels(self):
        if self.selection()[0] == self.selection()[1]:
            self.message.setText("Select the wording to annotate first.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Apply several labels to this passage")
        dialog.resize(430, 500)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        for entry in entries(self.result, "labels", active_only=True):
            item = QListWidgetItem(entry["name"])
            item.setData(Qt.UserRole, entry["id"])
            item.setToolTip(entry["definition"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.apply_labels([listing.item(i).data(Qt.UserRole) for i in range(listing.count()) if listing.item(i).checkState() == Qt.Checked])
        dialog.deleteLater()

    @writable
    def add_turn_range(self):
        row = self.turn.currentData()
        last = self.end_turn.currentData()
        if last is None:
            last = row
        try:
            self.add_records([create_range_annotation(self.result, row, last, self.new_label.currentData(), author=self.reviewer.text().strip())])
        except (ValueError, IndexError) as exc:
            self.message.setText(str(exc))

    @writable
    def add_time_event(self):
        row = self.turn.currentData()
        if row is None:
            return
        segment = self.result["segments"][row]
        dialog = QDialog(self)
        dialog.setWindowTitle("Annotate a timed event")
        form = QFormLayout(dialog)
        start, end = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, value in ((start, segment["start"]), (end, segment["end"])):
            spin.setRange(0, 9999999)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setValue(value)
        form.addRow("Label", QLabel(self.new_label.currentText()))
        form.addRow("Start", start)
        form.addRow("End", end)
        hint = QLabel("No transcript words are required. The selected turn remains the navigation reference.")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        while dialog.exec() == QDialog.Accepted:
            try:
                a = create_time_annotation(self.result, row, start.value(), end.value(), self.new_label.currentData(), author=self.reviewer.text().strip())
            except ValueError as exc:
                hint.setText(str(exc))
                continue
            self.add_records([a])
            break
        dialog.deleteLater()

    @writable
    def find_candidates(self):
        count = suggest(self.result)
        self.refresh()
        self.message.setText(f"{count} new candidates. Literal matches only; confirm their occurrence and function against the audio.")
        self.changed.emit()

    def listen_turn(self):
        a = self.annotation()
        row = self.turn.currentData()
        if a:
            self.playRange.emit(a["start"], a["end"])
        elif row is not None:
            segment = self.result["segments"][row]
            self.playRange.emit(segment["start"], segment["end"])

    def select_id(self, identity):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.UserRole) == identity:
                self.table.setCurrentCell(row, 0)
                self.table.selectRow(row)
                self.select_annotation()
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def select_annotation(self):
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            return
        self.flush_note()
        self.current_id = self.table.item(row, 0).data(Qt.UserRole)
        a = self.annotation()
        if not a:
            return
        self.loading = True
        self.turn.setCurrentIndex(self.turn.findData(a["segment"]))
        self.show_turn()
        self.kind.setCurrentIndex(self.kind.findData(a["label_id"]))
        self.function.setCurrentIndex(self.function.findData(a["function_id"]))
        self.status.setCurrentText(a["status"])
        self.certainty.setCurrentText(a.get("certainty", "Unspecified"))
        self.author.setText(a.get("author", ""))
        self.start.setValue(a["start"])
        self.end.setValue(a["end"])
        self.note.setPlainText(a["note"])
        count = len(a.get("spans", []))
        self.timing.setText(f"{count} source passages. " if count else "")
        self.timing.setText(self.timing.text() + ("Turn-level times; refine while listening." if a["timing"] == "turn" else "Times set manually."))
        def date_text(value):
            try:
                return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M") if value else "unknown"
            except ValueError:
                return value
        self.dates.setText("Created: " + date_text(a.get("created_at", "")) + "\nModified: " + date_text(a.get("modified_at", "")))
        self.dates.setToolTip("Created: " + a.get("created_at", "") + "\nModified: " + a.get("modified_at", ""))
        self.related.clear()
        linked = set(a.get("related_ids", []))
        for other in self.result.get("annotations", []):
            if other["id"] in linked:
                self.related.addItem(annotation_name(self.result, other) + ": " + (other["quote"][:60] or "Timed event"), other["id"])
        self.go_related.setEnabled(self.related.count() > 0)
        if is_current(a, self.result) and a.get("anchor_type") != "time":
            span = a.get("spans", [a])[0]
            text = self.passage.toPlainText()
            cursor = self.passage.textCursor()
            cursor.setPosition(len(text[:span["start_char"]].encode("utf-16-le")) // 2)
            cursor.setPosition(len(text[:span["end_char"]].encode("utf-16-le")) // 2, QTextCursor.KeepAnchor)
            self.passage.setTextCursor(cursor)
        elif not is_current(a, self.result):
            self.message.setText("Source wording changed. Reattach this annotation to a current selection before confirming it.")
        self.reattach.setEnabled(a.get("anchor_type") != "time")
        self.editor.setEnabled(True)
        self.loading = False
        self.set_read_only(self.read_only)

    @writable
    def update_annotation(self, *_):
        a = self.annotation()
        if self.loading or not a:
            return
        self.flush_note()
        status = self.status.currentText()
        if not is_current(a, self.result):
            status = "needs review"
            self.status.blockSignals(True)
            self.status.setCurrentText(status)
            self.status.blockSignals(False)
        a.update(label=self.kind.currentText(), label_id=self.kind.currentData(),
                 function=self.function.currentText(), function_id=self.function.currentData(),
                 status=status, certainty=self.certainty.currentText(), author=self.author.text().strip())
        touch_annotation(a)
        self.refresh()
        self.changed.emit()

    @writable
    def note_changed(self):
        a = self.annotation()
        if self.loading or not a:
            return
        a["note"] = self.note.toPlainText()
        touch_annotation(a)
        self.note_timer.start()

    @writable
    def author_changed(self, text):
        a = self.annotation()
        if self.loading or not a:
            return
        a["author"] = text.strip()
        touch_annotation(a)
        self.note_timer.start()

    @writable
    def update_timing(self, *_):
        a = self.annotation()
        if self.loading or not a:
            return
        if self.end.value() <= self.start.value():
            self.message.setText("End must be later than start. The previous times are retained.")
            self.loading = True
            self.start.setValue(a["start"])
            self.end.setValue(a["end"])
            self.loading = False
            return
        a.update(start=self.start.value(), end=self.end.value(), timing="manual")
        touch_annotation(a)
        self.timing.setText("Times set manually.")
        self.changed.emit()

    @writable
    def reattach_selection(self):
        a = self.annotation()
        start, end = self.selection()
        if not a or start == end:
            self.message.setText("Select the current passage before reattaching.")
            return
        row = self.turn.currentData()
        segment = self.result["segments"][row]
        a.pop("spans", None)
        a.update(segment=row, turn_id=segment["turn_id"], anchor_type="text", start_char=start, end_char=end,
                 quote=segment["text"][start:end], text_sha256=fingerprint(segment["text"]), status="suggested",
                 start=float(segment["start"]), end=float(segment["end"]), timing="turn")
        touch_annotation(a)
        self.refresh()
        self.select_id(a["id"])
        self.changed.emit()

    @writable
    def batch_status(self, status):
        selected = self.selected_ids()
        if not selected:
            self.message.setText("Select one or more annotation rows first.")
            return
        self.flush_note()
        stale = 0
        for a in self.result.get("annotations", []):
            if a["id"] in selected:
                a["status"] = status if is_current(a, self.result) else "needs review"
                stale += a["status"] == "needs review"
                touch_annotation(a)
        self.refresh()
        self.changed.emit()
        self.message.setText(f"Updated {len(selected)} annotations." + (f" {stale} changed-source annotations still need reattachment." if stale else ""))

    @writable
    def apply_function(self, identity):
        selected = self.selected_ids()
        entry = resolve_entry(self.result, "functions", identity)
        if not selected or not entry:
            return
        for a in self.result.get("annotations", []):
            if a["id"] in selected:
                a.update(function_id=identity, function=entry["name"])
                touch_annotation(a)
        self.refresh()
        self.changed.emit()

    @writable
    def apply_categories(self):
        selected = self.selected_ids()
        if not selected:
            return
        self.flush_note()
        for a in self.result.get("annotations", []):
            if a["id"] in selected:
                a.update(label=self.kind.currentText(), label_id=self.kind.currentData(),
                         function=self.function.currentText(), function_id=self.function.currentData())
                touch_annotation(a)
        self.refresh()
        self.changed.emit()

    def next_unresolved(self):
        pending = [a for a in self.filtered_annotations() if a["status"] in ("suggested", "needs review")]
        if not pending:
            self.message.setText("No unresolved annotations match the current filters.")
            return
        current = next((i for i, a in enumerate(pending) if a["id"] == self.current_id), -1)
        self.select_id(pending[(current + 1) % len(pending)]["id"])

    @writable
    def duplicate_selected(self):
        selected = self.selected_ids()
        self.add_records([duplicate_annotation(a) for a in self.result.get("annotations", []) if a["id"] in selected])

    @writable
    def link_selected(self):
        selected = self.selected_ids()
        if len(selected) < 2:
            self.message.setText("Select at least two annotations to link them.")
            return
        self.flush_note()
        for a in self.result.get("annotations", []):
            if a["id"] in selected:
                a["related_ids"] = sorted((set(a.get("related_ids", [])) | selected) - {a["id"]})
                touch_annotation(a)
        self.changed.emit()
        self.select_annotation()
        self.message.setText(f"Linked {len(selected)} annotations. Use Related to move between them.")

    @writable
    def unlink_selected(self):
        selected = self.selected_ids()
        if not selected:
            return
        self.flush_note()
        for a in self.result.get("annotations", []):
            previous = a.get("related_ids", [])
            revised = [] if a["id"] in selected else [identity for identity in previous if identity not in selected]
            if revised != previous:
                a["related_ids"] = revised
                touch_annotation(a)
        self.changed.emit()
        self.select_annotation()

    def go_to_related(self):
        identity = self.related.currentData()
        if identity and identity in {a["id"] for a in self.filtered_annotations()}:
            self.select_id(identity)
        elif identity:
            self.message.setText("The linked annotation is hidden by the current filters. Clear the filters to show it.")

    @writable
    def delete_selected(self):
        selected = self.selected_ids()
        if not selected:
            return
        self.flush_note()
        self.result["annotations"] = [a for a in self.result.get("annotations", []) if a["id"] not in selected]
        for a in self.result["annotations"]:
            previous = a.get("related_ids", [])
            revised = [identity for identity in previous if identity not in selected]
            if revised != previous:
                a["related_ids"] = revised
                touch_annotation(a)
        self.current_id = None
        self.refresh()
        self.changed.emit()

    @writable
    def edit_codebook(self):
        self.flush_note()
        dialog = CodebookDialog(ensure_codebook(self.result), self)
        if dialog.exec() == QDialog.Accepted:
            self.result["annotation_codebook"] = copy.deepcopy(dialog.book)
            migrate_annotations(self.result)
            self.populate_categories()
            self.refresh()
            if self.current_id:
                self.select_id(self.current_id)
            self.changed.emit()
        dialog.deleteLater()

    def export_filtered(self):
        self.flush_note()
        directory = QFileDialog.getExistingDirectory(self, "Export currently filtered annotations")
        if not directory:
            return
        folder = Path(directory) / ("annotations-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        try:
            export_annotations(self.result, folder, selected_ids={a["id"] for a in self.filtered_annotations()})
            self.message.setText("Exported filtered annotations and codebook to " + str(folder))
        except (ValueError, OSError) as exc:
            self.message.setText("Could not export annotations: " + str(exc))
