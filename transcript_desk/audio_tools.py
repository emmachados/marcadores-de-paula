"""Bounded interval playback and a waveform decoded outside the GUI process."""
from __future__ import annotations

import array
import math
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                             QPushButton, QSlider, QVBoxLayout, QWidget)

from transcript_desk.runtime import tool_path, start_process


class Waveform(QWidget):
    selected = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(85)
        self.setMaximumHeight(100)
        self.setAccessibleName('Waveform: drag to select an audio interval')
        self.peaks = array.array('f')
        self.offset, self.seconds = 0.0, 60.0
        self.interval = (0.0, 0.0)
        self.position, self.anchor = 0.0, None

    def time_at(self, x):
        duration = len(self.peaks) / 100
        return max(0, min(duration, self.offset + x / max(1, self.width()) * self.seconds))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.peaks:
            self.anchor = self.time_at(event.position().x())

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            other = self.time_at(event.position().x())
            self.interval = min(self.anchor, other), max(self.anchor, other)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.anchor is not None:
            other = self.time_at(event.position().x())
            start, end = min(self.anchor, other), max(self.anchor, other)
            self.anchor = None
            if end - start >= .01:
                self.interval = start, end
                self.selected.emit(start, end)
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        width, height = self.width(), self.height()
        scale = width / max(.01, self.seconds)
        left, right = ((value - self.offset) * scale for value in self.interval)
        painter.fillRect(round(left), 0, round(right - left), height, QColor(80, 135, 200, 65))
        painter.setPen(QPen(self.palette().text().color(), 1))
        if self.peaks:
            for x in range(width):
                first = max(0, int((self.offset + x / scale) * 100))
                last = min(len(self.peaks), max(first + 1, int((self.offset + (x + 1) / scale) * 100)))
                peak = max(self.peaks[first:last], default=0)
                amplitude = min(1, peak) * (height / 2 - 10)
                painter.drawLine(x, round(height / 2 - amplitude), x, round(height / 2 + amplitude))
        else:
            painter.drawText(self.rect(), Qt.AlignCenter, 'Load waveform to select boundaries visually')
        painter.setPen(QPen(QColor('#D88051'), 2))
        x = round((self.position - self.offset) * scale)
        painter.drawLine(x, 0, x, height)


class AudioTools(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.process = window, None
        self.active_interval = None
        self.buffer = b''
        self.samples = array.array('f')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.expand = QCheckBox('Waveform and interval review')
        top.addWidget(self.expand)
        self.previous, self.next = QPushButton('Previous turn'), QPushButton('Next turn')
        self.previous.clicked.connect(lambda: self.move_turn(-1))
        self.next.clicked.connect(lambda: self.move_turn(1))
        top.addWidget(self.previous)
        top.addWidget(self.next)
        self.skip = QDoubleSpinBox()
        self.skip.setRange(.5, 60)
        self.skip.setValue(3)
        self.skip.setSuffix(' s')
        top.addWidget(QLabel('Replay'))
        top.addWidget(self.skip)
        top.addStretch()
        layout.addLayout(top)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.start, self.end, self.context = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.start, self.end, self.context):
            spin.setRange(0, 999999)
            spin.setDecimals(2)
            spin.setSuffix(' s')
            spin.setKeyboardTracking(False)
        self.context.setRange(0, 30)
        self.loop = QCheckBox('Loop')
        self.play = QPushButton('Play interval')
        self.play.clicked.connect(lambda: self.play_range(self.start.value(), self.end.value()))
        self.current = QPushButton('Use selected turn')
        self.current.clicked.connect(self.use_turn)
        for widget in (QLabel('Start'), self.start, QLabel('End'), self.end, QLabel('Context'), self.context, self.loop, self.play, self.current):
            row.addWidget(widget)
        body.addLayout(row)
        self.wave = Waveform()
        self.wave.selected.connect(self.set_interval)
        body.addWidget(self.wave)
        row = QHBoxLayout()
        self.load_button = QPushButton('Load waveform')
        self.load_button.clicked.connect(self.load_waveform)
        self.zoom = QComboBox()
        self.zoom.addItem('Whole recording', 0)
        for seconds in (5, 15, 30, 60, 120, 300, 900, 3600):
            self.zoom.addItem(f'{seconds} s visible', seconds)
        self.zoom.setCurrentIndex(0)
        self.zoom.currentIndexChanged.connect(self.change_view)
        self.scroll = QSlider(Qt.Horizontal)
        self.scroll.setRange(0, 0)
        self.scroll.valueChanged.connect(self.change_view)
        self.message = QLabel()
        for widget in (self.load_button, self.zoom, self.scroll, self.message):
            row.addWidget(widget, 1 if widget is self.scroll else 0)
        body.addLayout(row)
        layout.addWidget(self.body)
        self.body.hide()
        self.expand.toggled.connect(self.body.setVisible)
        self.start.valueChanged.connect(self.update_interval)
        self.end.valueChanged.connect(self.update_interval)
        window.player.player.positionChanged.connect(self.position)
        window.player.back.clicked.disconnect()
        window.player.back.clicked.connect(self.replay)
        self.skip.valueChanged.connect(lambda value: window.player.back.setText(f'−{value:g} s'))

    def reset(self):
        self.active_interval = None
        if self.process is not None:
            process, self.process = self.process, None
            process.readyReadStandardOutput.disconnect()
            process.finished.disconnect()
            process.errorOccurred.disconnect()
            if process.state() != QProcess.NotRunning:
                process.finished.connect(process.deleteLater)
                process.kill()
            else:
                process.deleteLater()
        self.load_button.setEnabled(True)
        self.buffer, self.samples = b'', array.array('f')
        self.wave.peaks = array.array('f')
        self.wave.offset = 0
        self.wave.update()
        self.message.clear()

    def set_interval(self, start, end):
        self.start.setValue(start)
        self.end.setValue(end)

    def update_interval(self):
        self.wave.interval = self.start.value(), self.end.value()
        self.wave.update()

    def use_turn(self):
        row = self.window.transcript.currentRow()
        if self.window.result and 0 <= row < len(self.window.result['segments']):
            segment = self.window.result['segments'][row]
            self.set_interval(segment['start'], segment['end'])
            self.scroll.setValue(max(0, int(segment['start'] - 1)))

    def move_turn(self, direction):
        if self.window.result and self.window.result['segments']:
            row = max(0, min(len(self.window.result['segments']) - 1, self.window.transcript.currentRow() + direction))
            self.window.transcript.setCurrentCell(row, 3)
            self.window.transcript.scrollToItem(self.window.transcript.item(row, 3))
            self.use_turn()
            self.play_range(self.start.value(), self.end.value())

    def play_range(self, start, end):
        if not math.isfinite(start + end) or end <= start:
            self.message.setText('End must follow start.')
            return
        self.set_interval(start, end)
        self.active_interval = (max(0, start - self.context.value()), end + self.context.value())
        player = self.window.player.player
        player.setPosition(round(self.active_interval[0] * 1000))
        player.play()

    def replay(self):
        self.active_interval = None
        player = self.window.player.player
        player.setPosition(max(0, player.position() - round(self.skip.value() * 1000)))
        player.play()

    def position(self, milliseconds):
        seconds = milliseconds / 1000
        self.wave.position = seconds
        self.wave.update()
        if self.active_interval and seconds >= self.active_interval[1] - .02:
            if self.loop.isChecked():
                self.window.player.player.setPosition(round(self.active_interval[0] * 1000))
                self.window.player.player.play()
            else:
                self.active_interval = None
                self.window.player.player.pause()

    def change_view(self):
        self.wave.offset = self.scroll.value()
        self.wave.seconds = self.zoom.currentData() or max(1, len(self.wave.peaks) / 100)
        if not self.zoom.currentData():
            self.wave.offset = 0
        self.wave.update()

    def load_waveform(self):
        source = self.window.player.player.source().toLocalFile()
        ffmpeg = tool_path('ffmpeg')
        if not source or not Path(source).is_file() or not ffmpeg:
            self.message.setText('Locate audio and install the media tools first.')
            return
        if self.process is not None:
            return
        self.buffer, self.samples = b'', array.array('f')
        self.wave.peaks = array.array('f')
        process = QProcess(self)
        self.process = process
        process.setProcessChannelMode(QProcess.SeparateChannels)
        process.readyReadStandardOutput.connect(lambda process=process: self.consume(process))
        process.finished.connect(lambda code, status, process=process: self.finished(code, status, process=process))
        process.errorOccurred.connect(lambda error, process=process: self.failed(error, process))
        self.message.setText('Decoding waveform…')
        self.load_button.setEnabled(False)
        start_process(process, [ffmpeg, '-nostdin', '-v', 'error', '-i', source, '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1'])

    def consume(self, process=None):
        process = process or self.process
        if process is None or process is not self.process:
            return
        self.buffer += bytes(process.readAllStandardOutput())
        size = len(self.buffer) // 4 * 4
        values = array.array('f')
        values.frombytes(self.buffer[:size])
        self.buffer = self.buffer[size:]
        if sys.byteorder != 'little':
            values.byteswap()
        self.samples.extend(values)
        blocks = len(self.samples) // 80
        for i in range(blocks):
            self.wave.peaks.append(max(abs(value) for value in self.samples[i * 80:(i + 1) * 80]))
        del self.samples[:blocks * 80]

    def failed(self, error, process):
        if error == QProcess.FailedToStart and process is self.process:
            self.process = None
            process.deleteLater()
            self.load_button.setEnabled(True)
            self.message.setText('Waveform decoder could not start. Check the media tools and try again.')

    def finished(self, code, *_, process=None):
        if process is not self.process:
            return
        self.consume(process)
        self.process = None
        self.load_button.setEnabled(True)
        if process is not None:
            self.message.setText(f'{len(self.wave.peaks)/100:.1f} s decoded' if code == 0 else 'Waveform decoding failed; playback remains available.')
            process.deleteLater()
        self.scroll.setRange(0, max(0, int(len(self.wave.peaks) / 100 - 1)))
        self.change_view()
        self.wave.update()

    def shutdown(self):
        for process in self.findChildren(QProcess):
            if process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(2000)
