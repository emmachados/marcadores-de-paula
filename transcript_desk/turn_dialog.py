"""Controls for editing a selected transcript turn."""
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout)


class TurnDialog(QDialog):
    def __init__(self, result, row, position, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit selected turn')
        self.resize(620, 430)
        segment = result['segments'][row]
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.operation = QComboBox()
        self.operation.addItems(['Assign speaker', 'Change start and end', 'Split turn', 'Merge with next turn'])
        form.addRow('Action', self.operation)
        self.speaker = QComboBox()
        self.speaker.addItem('Unassigned', None)
        ids = sorted({s['speaker'] for s in result['segments'] if s.get('speaker')} | set(result.get('speaker_names', {})))
        for speaker in ids:
            name = result.get('speaker_names', {}).get(speaker, speaker)
            self.speaker.addItem(f'{name} ({speaker})', speaker)
        self.speaker.addItem('New speaker…', '__new__')
        self.speaker.setCurrentIndex(max(0, self.speaker.findData(segment.get('speaker'))))
        form.addRow('Speaker for this turn', self.speaker)
        self.name = QLineEdit()
        self.name.setPlaceholderText('Name of the new speaker')
        form.addRow('New speaker name', self.name)
        self.start, self.end, self.split_time = (QDoubleSpinBox() for _ in range(3))
        for spin in (self.start, self.end, self.split_time):
            spin.setRange(0, 9999999)
            spin.setDecimals(3)
            spin.setSuffix(' s')
        self.start.setValue(segment['start'])
        self.end.setValue(segment['end'])
        self.split_time.setValue(position if segment['start'] < position < segment['end'] else (segment['start'] + segment['end']) / 2)
        form.addRow('Start', self.start)
        form.addRow('End', self.end)
        form.addRow('Split time', self.split_time)
        layout.addLayout(form)
        self.passage = QPlainTextEdit(segment['text'])
        self.passage.setReadOnly(True)
        self.passage.setAccessibleName('Place the cursor at the text split')
        layout.addWidget(self.passage)
        playback = QHBoxLayout()
        self.play = QPushButton('Play / pause')
        self.use_position = QPushButton('Use playback time for split')
        if parent is not None:
            self.play.setEnabled(parent.player.play.isEnabled())
            self.play.clicked.connect(parent.player.toggle)
            self.use_position.clicked.connect(lambda: self.split_time.setValue(parent.player.player.position() / 1000))
        else:
            self.play.setEnabled(False)
        playback.addWidget(self.play)
        playback.addWidget(self.use_position)
        playback.addStretch()
        layout.addLayout(playback)
        self.help = QLabel()
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.operation.currentIndexChanged.connect(self.refresh)
        self.speaker.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        operation = self.operation.currentIndex()
        self.speaker.setEnabled(operation == 0)
        self.name.setEnabled(operation == 0 and self.speaker.currentData() == '__new__')
        self.start.setEnabled(operation == 1)
        self.end.setEnabled(operation == 1)
        self.split_time.setEnabled(operation == 2)
        self.use_position.setEnabled(operation == 2 and self.play.isEnabled())
        self.passage.setEnabled(operation == 2)
        self.help.setText([
            'Only this turn changes speaker. A new name that matches an existing speaker reuses that speaker. Review → Manage speakers renames a speaker throughout the recording.',
            'Enter times in seconds. Existing word timings are kept as original data; affected annotations will need review.',
            'Click between the passages in the text above and set the corresponding audio time. Both pieces keep their exact wording. The initial time is a suggestion to check against the audio.',
            'The selected and following turns must have the same speaker. Wording is kept; affected annotations will need review.'
        ][operation])

    def character(self):
        text = self.passage.toPlainText()
        units = self.passage.textCursor().position()
        return len(text.encode('utf-16-le')[:units * 2].decode('utf-16-le'))
