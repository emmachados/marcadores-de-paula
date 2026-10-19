"""Edit a project codebook without changing the project until Save is accepted."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                              QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
                              QDialogButtonBox, QLabel, QFileDialog, QPlainTextEdit)

from transcript_desk.codebook import (validate_codebook, new_entry, merge_codebook,
                                     read_codebook, write_codebook)


class CodebookDialog(QDialog):
    def __init__(self, book, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Project annotation codebook")
        self.resize(1100, 660)
        self.book = copy.deepcopy(book)
        layout = QVBoxLayout(self)
        hint = QLabel("Edit names and definitions for this project. Identifiers remain unchanged when names change. "
                      "Archive a category to hide it from new annotations while keeping earlier records.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        self.tables = {}
        for kind, title in (("labels", "Labels"), ("functions", "Functions")):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            table = QTableWidget(0, 7)
            table.setHorizontalHeaderLabels(["Name", "Definition", "Colour", "Shortcut", "Active", "Group", "Favourite"])
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setWordWrap(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            table.setColumnWidth(0, 220)
            table.setColumnWidth(2, 85)
            table.setColumnWidth(3, 100)
            table.setColumnWidth(4, 55)
            table.setColumnWidth(5, 95)
            table.setColumnWidth(6, 90)
            self.tables[kind] = table
            page_layout.addWidget(table)
            add = QPushButton("Add " + title.lower().rstrip("s"))
            add.clicked.connect(lambda checked=False, category=kind: self.add_entry(category))
            page_layout.addWidget(add)
            self.tabs.addTab(page, title)
        self.forms = QPlainTextEdit()
        self.forms.setPlaceholderText("One literal candidate form per line. Matching is case-insensitive; functions require manual review.")
        self.tabs.addTab(self.forms, "Candidate forms")
        layout.addWidget(self.tabs)
        self.message = QLabel("Shortcuts act while the Annotations tab has focus. Use combinations such as Alt+1.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        tools = QHBoxLayout()
        import_button = QPushButton("Import codebook…")
        import_button.clicked.connect(self.import_book)
        export_button = QPushButton("Export codebook…")
        export_button.clicked.connect(self.export_book)
        tools.addWidget(import_button)
        tools.addWidget(export_button)
        tools.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        tools.addWidget(buttons)
        layout.addLayout(tools)
        self.populate()

    def append_entry(self, table, entry):
        row = table.rowCount()
        table.insertRow(row)
        for column, field in enumerate(("name", "definition", "color", "shortcut")):
            item = QTableWidgetItem(entry[field])
            item.setData(Qt.UserRole, entry["id"])
            item.setToolTip(entry[field])
            table.setItem(row, column, item)
        active = QTableWidgetItem()
        active.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        active.setCheckState(Qt.Unchecked if entry["archived"] else Qt.Checked)
        table.setItem(row, 4, active)
        table.setItem(row, 5, QTableWidgetItem(entry.get("group", "")))
        favourite = QTableWidgetItem()
        favourite.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        favourite.setCheckState(Qt.Checked if entry.get("favourite", False) else Qt.Unchecked)
        table.setItem(row, 6, favourite)

    def populate(self):
        for kind, table in self.tables.items():
            table.setRowCount(0)
            for entry in self.book[kind]:
                self.append_entry(table, entry)
        self.forms.setPlainText("\n".join(self.book.get("candidate_forms", [])))

    def add_entry(self, kind):
        table = self.tables[kind]
        base = "New label" if kind == "labels" else "New function"
        names = {table.item(row, 0).text() for row in range(table.rowCount())}
        name, count = base, 2
        while name in names:
            name = f"{base} {count}"
            count += 1
        self.append_entry(table, new_entry(name))
        table.setCurrentCell(table.rowCount() - 1, 0)
        table.editItem(table.currentItem())

    def collect(self):
        book = {"schema": 1, "candidate_forms": list(dict.fromkeys(line.strip() for line in self.forms.toPlainText().splitlines() if line.strip()))}
        shortcuts = set()
        for kind, table in self.tables.items():
            book[kind] = []
            for row in range(table.rowCount()):
                entry = {field: table.item(row, col).text().strip() for col, field in enumerate(("name", "definition", "color", "shortcut"))}
                entry["id"] = table.item(row, 0).data(Qt.UserRole)
                entry["archived"] = table.item(row, 4).checkState() != Qt.Checked
                entry["group"] = table.item(row, 5).text().strip()
                entry["favourite"] = table.item(row, 6).checkState() == Qt.Checked
                if entry["shortcut"]:
                    sequence = QKeySequence.fromString(entry["shortcut"], QKeySequence.PortableText)
                    if sequence.isEmpty():
                        raise ValueError("A shortcut could not be read: " + entry["shortcut"])
                    entry["shortcut"] = sequence.toString(QKeySequence.PortableText)
                    if not entry["archived"] and entry["shortcut"] in shortcuts:
                        raise ValueError("Use a different shortcut for each active label or function.")
                    if not entry["archived"]:
                        shortcuts.add(entry["shortcut"])
                book[kind].append(entry)
        return validate_codebook(book)

    def save(self):
        try:
            self.book = self.collect()
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.accept()

    def import_book(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import annotation codebook", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.book = merge_codebook(self.collect(), read_codebook(path))
            self.populate()
            self.message.setText("Imported categories are merged by identifier. Existing project categories are kept. Save to apply.")
        except (ValueError, OSError, TypeError) as exc:
            self.message.setText("Could not import codebook: " + str(exc))

    def export_book(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export annotation codebook", "annotation-codebook.json", "JSON (*.json)")
        if not path:
            return
        try:
            write_codebook(path, self.collect())
            self.message.setText("Codebook exported to " + path)
        except (ValueError, OSError) as exc:
            self.message.setText("Could not export codebook: " + str(exc))
