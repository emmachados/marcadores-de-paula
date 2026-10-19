"""Local audio playback and highlighting of aligned transcript words."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, QPersistentModelIndex
from PySide6.QtGui import QBrush, QColor, QPalette, QTextCharFormat, QTextCursor, QTextDocument
from shiboken6 import isValid
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                              QPushButton, QSlider, QStyledItemDelegate, QStyle,
                              QStyleOptionViewItem, QVBoxLayout, QWidget)


def active_text(segments, seconds):
    """Return a turn and exact character span; edited text is highlighted as a whole turn."""
    for row, segment in enumerate(segments):
        if segment["start"] <= seconds < segment["end"]:
            offset = 0
            text = segment["text"]
            if not segment.get("text_edited") and not segment.get("word_text_mismatch"):
                for word in segment.get("words", []):
                    token = word.get("word", "").strip()
                    index = text.find(token, offset) if token else -1
                    if index < 0:
                        return row, None
                    offset = index + len(token)
                    if word.get("start") is not None and word.get("end") is not None:
                        if word["start"] <= seconds < word["end"]:
                            return row, (index, len(token))
            return row, None
    return -1, None


class TranscriptDelegate(QStyledItemDelegate):
    draftChanged = Signal()

    def __init__(self, table):
        super().__init__(table)
        self.active_row = -1
        self.span = None
        self.editor = None
        self.editor_index = QPersistentModelIndex()

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent) if index.column() == 2 else QPlainTextEdit(parent)
        editor.setAccessibleName("Edit speaker name" if index.column() == 2 else "Edit transcript wording")
        if isinstance(editor, QPlainTextEdit):
            editor.setTabChangesFocus(True)
            editor.document().setDocumentMargin(0)
            table = self.parent()
            editor.setStyleSheet(f"QPlainTextEdit, QPlainTextEdit:focus {{ padding: 2px; font-size: {table.font().pixelSize()}px; }}")
            table.setRowHeight(index.row(), max(96, table.rowHeight(index.row())))
        self.editor, self.editor_index = editor, QPersistentModelIndex(index)
        editor.textChanged.connect(lambda *_: self.draftChanged.emit())
        return editor

    def setEditorData(self, editor, index):
        # Qt may call this for decoration-role changes while the user is typing.
        # A live draft belongs to the editor until it is committed or cancelled.
        if editor.property('draft_initialized'):
            return
        editor.blockSignals(True)
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText(index.data())
        else:
            editor.setText(index.data())
        editor.blockSignals(False)
        editor.setProperty('draft_initialized', True)

    def setModelData(self, editor, model, index):
        value = editor.toPlainText() if isinstance(editor, QPlainTextEdit) else editor.text()
        if value != index.data():
            model.setData(index, value, Qt.EditRole)

    def draft(self):
        if self.editor is not None and isValid(self.editor) and self.editor_index.isValid():
            value = self.editor.toPlainText() if isinstance(self.editor, QPlainTextEdit) else self.editor.text()
            return self.editor_index.row(), self.editor_index.column(), value
        return None

    def finish(self):
        editor = self.editor
        if editor is not None and isValid(editor) and self.editor_index.isValid():
            self.commitData.emit(editor)
            if isValid(editor) and self.editor is editor:
                self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)

    def destroyEditor(self, editor, index):
        if editor is self.editor:
            self.editor = None
            self.editor_index = QPersistentModelIndex()
            # Escape must also replace any checkpoint of the cancelled edit.
            self.draftChanged.emit()
        super().destroyEditor(editor, index)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style()
        if index.row() == self.active_row:
            from transcript_desk.theme import palette_tokens
            mode = 'dark' if opt.palette.color(QPalette.Base).lightness() < 128 else 'light'
            opt.backgroundBrush = QBrush(QColor(palette_tokens(mode)['selection']))
        if index.column() != 3:
            return style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        text = opt.text
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        rect = option.rect.adjusted(10, 8, -10, -8)
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(opt.font)
        document.setPlainText(text)
        document.setTextWidth(rect.width())
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        fmt.setForeground(opt.palette.color(QPalette.Text))
        cursor.setCharFormat(fmt)
        if index.row() == self.active_row and self.span:
            # Qt cursor offsets are UTF-16 units; Python offsets are Unicode characters.
            start, length = self.span
            cursor.setPosition(len(text[:start].encode("utf-16-le")) // 2)
            cursor.setPosition(len(text[:start + length].encode("utf-16-le")) // 2, QTextCursor.KeepAnchor)
            from transcript_desk.theme import palette_tokens
            mode = "dark" if opt.palette.color(QPalette.Base).lightness() < 128 else "light"
            colors = palette_tokens(mode)
            fmt.setBackground(QColor(colors["highlight"]))
            fmt.setForeground(QColor(colors["highlight_ink"]))
            fmt.setFontWeight(600)
            cursor.setCharFormat(fmt)
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.topLeft())
        document.drawContents(painter)
        painter.restore()


class ReviewPlayer(QWidget):
    positionChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("player")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 12)
        self.caption = QLabel("Select a transcript to listen and review")
        self.caption.setObjectName("muted")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        row = QHBoxLayout()
        self.back = QPushButton("−3 s")
        self.back.setToolTip("Replay the previous three seconds")
        self.back.clicked.connect(lambda: self.player.setPosition(max(0, self.player.position() - 3000)))
        self.play = QPushButton("Play")
        self.play.setMinimumWidth(64)
        self.play.setToolTip("Play or pause (F4)")
        self.play.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName("Playback position")
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.clock = QLabel("00:00 / 00:00")
        self.rate = QComboBox()
        self.rate.setAccessibleName("Playback speed")
        for speed in (.5, .75, 1, 1.25, 1.5, 2):
            self.rate.addItem(f"{speed:g}×", speed)
        self.rate.setCurrentIndex(2)
        self.rate.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(self.rate.currentData()))
        for widget in (self.back, self.play, self.slider, self.clock, self.rate):
            row.addWidget(widget, 1 if widget is self.slider else 0)
        layout.addLayout(row)
        self.follow = QCheckBox("Follow playback")
        self.follow.setChecked(True)
        layout.addWidget(self.follow)
        self.player.positionChanged.connect(self.position)
        self.player.durationChanged.connect(lambda ms: self.slider.setRange(0, ms))
        self.player.playbackStateChanged.connect(lambda state: self.play.setText("Pause" if state == QMediaPlayer.PlayingState else "Play"))
        self.player.errorOccurred.connect(lambda *_: self.caption.setText("Playback unavailable: " + self.player.errorString()))
        self.load(None)

    def load(self, path, title="Recording"):
        self.player.stop()
        available = bool(path and Path(path).is_file())
        self.player.setSource(QUrl.fromLocalFile(str(path)) if available else QUrl())
        self.caption.setText(title if available else "Audio unavailable. Use Locate audio to reconnect the original recording.")
        for widget in (self.back, self.play, self.slider, self.rate):
            widget.setEnabled(available)
        self.slider.setValue(0)
        self.clock.setText("00:00 / 00:00")

    def toggle(self):
        if not self.play.isEnabled():
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def position(self, milliseconds):
        if not self.slider.isSliderDown():
            self.slider.setValue(milliseconds)
        def time(ms):
            seconds = ms // 1000
            return f"{seconds // 60:02d}:{seconds % 60:02d}"
        self.clock.setText(f"{time(milliseconds)} / {time(self.player.duration())}")
        self.positionChanged.emit(milliseconds)
