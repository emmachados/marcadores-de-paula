"""Previewed transcript, annotation and speaker export presets."""
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                              QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                              QPlainTextEdit, QPushButton, QVBoxLayout)

from transcript_desk.codebook import entries
from transcript_desk.exports import FORMATS, speaker_name
from transcript_desk.text_tools import reading_view, speaker_ids


def normalise_preset(value):
    """Ignore malformed persisted settings without applying unexpected types."""
    if not isinstance(value, dict):
        return {}
    preset = {}
    for key in ('timestamps', 'grouped', 'paragraphs', 'separate_speakers',
                'annotation_tiers', 'marked_passages', 'analysis_tables'):
        if type(value.get(key)) is bool:
            preset[key] = value[key]
    for key in ('speakers', 'labels', 'statuses', 'formats'):
        values = value.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) and not (key == 'speakers' and item is None) for item in values):
            continue
        if key == 'statuses':
            values = [item for item in values if item in ('confirmed', 'suggested', 'rejected', 'needs review')]
        elif key == 'formats':
            values = [item for item in values if item in FORMATS]
        preset[key] = list(dict.fromkeys(values))
    return preset


class ExportDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Export reviewed transcript')
        self.resize(830, 690)
        layout = QVBoxLayout(self)
        preset_row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItems(['Current selection', 'Full timed review', 'Continuous speaker text', 'Confirmed annotations'])
        try:
            self.saved = normalise_preset(json.loads(window.settings.value('export_preset', '{}')))
        except (ValueError, TypeError):
            self.saved = {}
        if self.saved:
            self.preset.addItem('Saved preset')
        preset_row.addWidget(QLabel('Preset'))
        preset_row.addWidget(self.preset, 1)
        save = QPushButton('Remember these options')
        save.clicked.connect(self.remember)
        preset_row.addWidget(save)
        layout.addLayout(preset_row)
        row = QHBoxLayout()
        self.formats = {}
        for fmt in FORMATS:
            box = QCheckBox(fmt)
            preferred = (getattr(window, 'result_job', None) or {}).get('config', {}).get('formats', ['TXT', 'DOCX', 'JSON'])
            box.setChecked(fmt in preferred)
            self.formats[fmt] = box
            row.addWidget(box)
        layout.addLayout(row)
        lists = QHBoxLayout()
        self.speakers, self.labels = QListWidget(), QListWidget()
        self.speakers.setAccessibleName('Speakers included in export')
        self.labels.setAccessibleName('Annotation categories included in export')
        for identity in speaker_ids(window.result):
            item = QListWidgetItem(speaker_name(identity, window.result.get('speaker_names', {})))
            item.setData(Qt.UserRole, identity)
            item.setCheckState(Qt.Checked)
            self.speakers.addItem(item)
        for entry in entries(window.result, 'labels'):
            item = QListWidgetItem(entry['name'])
            item.setData(Qt.UserRole, entry['id'])
            item.setCheckState(Qt.Checked)
            self.labels.addItem(item)
        for title, widget in (('Speakers', self.speakers), ('Annotation categories', self.labels)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(widget)
            lists.addLayout(column)
        layout.addLayout(lists, 1)
        row = QHBoxLayout()
        self.times, self.grouped, self.paragraphs, self.separate = (QCheckBox(label) for label in
            ('Timestamps in TXT / DOCX', 'Group text by speaker', 'Keep turn paragraphs', 'One folder per speaker'))
        self.times.setChecked(True)
        self.paragraphs.setChecked(True)
        for widget in (self.times, self.grouped, self.paragraphs, self.separate):
            row.addWidget(widget)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.statuses = {}
        for status in ('confirmed', 'suggested', 'rejected', 'needs review'):
            box = QCheckBox(status.capitalize())
            box.setChecked(True)
            self.statuses[status] = box
            row.addWidget(box)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.tiers = QCheckBox('Annotation tiers in EAF / TextGrid')
        self.marked = QCheckBox('Annotated DOCX')
        self.analysis = QCheckBox('Concordance and count tables')
        for widget in (self.tiers, self.marked, self.analysis):
            row.addWidget(widget)
        layout.addLayout(row)
        self.preview_summary = QLabel()
        self.preview_summary.setWordWrap(True)
        layout.addWidget(self.preview_summary)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(140)
        self.preview.setAccessibleName('Text export preview')
        layout.addWidget(self.preview)
        self.message = QLabel('Timed formats keep the source intervals. Annotations marked for review are left out of the timing tiers.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Choose folder and export…')
        buttons.accepted.connect(self.export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self.update_preview)
        self.preset.currentIndexChanged.connect(self.apply_preset)
        for widget in (*self.formats.values(), self.times, self.grouped, self.paragraphs, self.separate,
                       *self.statuses.values(), self.tiers, self.marked, self.analysis):
            widget.toggled.connect(self.schedule_preview)
        self.speakers.itemChanged.connect(self.schedule_preview)
        self.labels.itemChanged.connect(self.schedule_preview)
        self.update_preview()

    def options(self):
        selected = lambda widget: [widget.item(i).data(Qt.UserRole) for i in range(widget.count()) if widget.item(i).checkState() == Qt.Checked]
        return {'speakers': selected(self.speakers), 'labels': selected(self.labels),
                'statuses': [status for status, box in self.statuses.items() if box.isChecked()],
                'timestamps': self.times.isChecked(), 'grouped': self.grouped.isChecked(),
                'paragraphs': self.paragraphs.isChecked(), 'separate_speakers': self.separate.isChecked(),
                'annotation_tiers': self.tiers.isChecked(), 'marked_passages': self.marked.isChecked(),
                'analysis_tables': self.analysis.isChecked()}

    def schedule_preview(self, *_):
        self.preview_timer.start()

    def update_preview(self, *_):
        self.preview_timer.stop()
        options = self.options()
        source = self.window.result
        speakers = set(options['speakers'])
        rows = [(i, segment) for i, segment in enumerate(source['segments']) if segment.get('speaker') in speakers]
        kept_rows = {i for i, _ in rows}
        kept_ids = {segment.get('turn_id') for _, segment in rows}
        count, omitted = 0, 0
        labels, statuses = set(options['labels']), set(options['statuses'])
        for annotation in source.get('annotations', []):
            if annotation['status'] not in statuses or annotation.get('label_id', annotation['label']) not in labels:
                continue
            if annotation.get('spans') and any(span['turn_id'] not in kept_ids for span in annotation['spans']):
                if any(span['turn_id'] in kept_ids for span in annotation['spans']):
                    omitted += 1
                continue
            count += annotation['segment'] in kept_rows
        self.preview_summary.setText(f'Text preview: {len(rows)} source turns; {count} annotations selected.' +
                                    (f' {omitted} multi-turn annotations excluded because some source turns are not selected.' if omitted else ''))
        rows.sort(key=lambda item: (item[1]['start'], item[1]['end'], item[0]))
        if options['grouped']:
            groups = {}
            for row, segment in rows:
                groups.setdefault(segment.get('speaker'), []).append((row, segment))
            rows = [item for group in groups.values() for item in group]
        # Preview only visible text. Word timings and annotation data are never copied.
        limited, budget = [], 12001
        for _, segment in rows:
            if budget <= 0:
                break
            limit = max(0, budget)
            limited.append({**segment, 'text': segment['text'][:limit]})
            budget -= len(segment['text']) + 2
        selected = {'segments': limited, 'speaker_names': source.get('speaker_names', {})}
        text, _ = reading_view(selected, timestamps=options['timestamps'], grouped=options['grouped'], paragraphs=options['paragraphs'])
        truncated = len(limited) < len(rows) or len(text) > 12000 or budget < 0
        self.preview.setPlainText(text[:12000] + ('\n[Preview truncated]' if truncated else ''))

    def remember(self):
        self.saved = {**self.options(), 'formats': [fmt for fmt, box in self.formats.items() if box.isChecked()]}
        self.window.settings.setValue('export_preset', json.dumps(self.saved))
        if self.preset.findText('Saved preset') < 0:
            self.preset.addItem('Saved preset')
        self.message.setText('These export options have been saved.')

    def apply_preset(self):
        index = self.preset.currentIndex()
        if index == 0:
            return
        options = self.saved if self.preset.currentText() == 'Saved preset' else {
            'timestamps': index != 2, 'grouped': index == 2, 'paragraphs': index != 2,
            'separate_speakers': False, 'statuses': ['confirmed'] if index == 3 else list(self.statuses),
            'annotation_tiers': index == 3, 'marked_passages': index == 3, 'analysis_tables': index == 3}
        toggles = (('timestamps', self.times), ('grouped', self.grouped), ('paragraphs', self.paragraphs),
                   ('separate_speakers', self.separate), ('annotation_tiers', self.tiers),
                   ('marked_passages', self.marked), ('analysis_tables', self.analysis))
        widgets = [widget for _, widget in toggles] + list(self.statuses.values()) + list(self.formats.values()) + [self.speakers, self.labels]
        previous = [widget.blockSignals(True) for widget in widgets]
        absent = []
        try:
            for key, widget in toggles:
                if key in options:
                    widget.setChecked(options[key])
            if 'statuses' in options:
                for status, box in self.statuses.items():
                    box.setChecked(status in options['statuses'])
            if 'formats' in options:
                for fmt, box in self.formats.items():
                    box.setChecked(fmt in options['formats'])
            for key, listing in (('speakers', self.speakers), ('labels', self.labels)):
                if key in options:
                    available = {listing.item(i).data(Qt.UserRole) for i in range(listing.count())}
                    missing = set(options[key]) - available
                    if missing:
                        absent.append(f'{len(missing)} saved {key} unavailable in this project')
                    for i in range(listing.count()):
                        item = listing.item(i)
                        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in options[key] else Qt.Unchecked)
        finally:
            for widget, blocked in zip(widgets, previous):
                widget.blockSignals(blocked)
        self.update_preview()
        self.message.setText(('Preset applied; ' + '; '.join(absent) + '. Select the needed entries before exporting.') if absent else
                             'Timed formats keep the source intervals. Annotations marked for review are left out of the timing tiers.')

    def export(self):
        formats = [fmt for fmt, box in self.formats.items() if box.isChecked()]
        if not formats or not self.options()['speakers']:
            self.message.setText('Select at least one format and one speaker.')
            return
        directory = QFileDialog.getExistingDirectory(self, 'Export destination', self.window.result_job['config']['output'])
        if directory and self.window.workspace.export_to(directory, formats, self.options()):
            self.accept()
