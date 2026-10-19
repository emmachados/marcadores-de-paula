"""Local, copyable processing error details, separate from exported diagnostics."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from transcript_desk.processing_errors import issue_text


class ProcessingIssueDialog(QDialog):
    def __init__(self, issue, parent=None):
        super().__init__(parent)
        self.issue = issue
        self.setWindowTitle(issue.get('title', 'Processing issue'))
        self.resize(660, 430)
        layout = QVBoxLayout(self)
        explanation = QLabel(issue_text(issue))
        explanation.setWordWrap(True)
        explanation.setTextFormat(Qt.PlainText)
        explanation.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(explanation)
        self.details = QPlainTextEdit(issue_text(issue, include_details=True))
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        copy = QPushButton('Copy details')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(issue_text(self.issue, include_details=True)))
        buttons.addButton(copy, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
