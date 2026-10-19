from __future__ import annotations

import json
import time
from datetime import datetime

from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                             QApplication, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from transcript_desk.runtime import (ENGINE_VERSION, child_environment, engine_dir,
                                     engine_python, engine_ready, runtime_root, setup_commands, start_process,
                                     python_link_recovery, engine_record)
from transcript_desk.setup_errors import installation_advice
from transcript_desk import __version__


def label(text, muted=False):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if muted:
        widget.setObjectName("muted")
    return widget


class SetupDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Set up Marcadores de Paula")
        self.resize(640, 700)
        self.process = None
        self.commands = []
        self.action = ""
        self.log_path = None
        self.step_number = 0
        self.step_total = 0
        self.step_started = 0
        self.step_output = ""
        self.recovered_python = None
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(1000)
        self.heartbeat.timeout.connect(self.update_step_status)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        title = label("Set up this computer")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(label("Media downloads work immediately. Transcription needs a one-time engine installation and model downloads.", True))
        layout.addWidget(label("Processing engine"))
        self.device = QComboBox()
        self.device.addItem("CPU · works without an NVIDIA graphics card", "cpu")
        self.device.addItem("NVIDIA GPU · requires a compatible card and driver", "cuda")
        self.device.setCurrentIndex(max(0, self.device.findData(parent.device)))
        layout.addWidget(self.device)
        layout.addWidget(label("The CPU option can take a long time with large models. The NVIDIA option installs CUDA-enabled libraries. Installation and the first transcription can download several gigabytes; keep this window open.", True))
        row = QHBoxLayout()
        self.engine_status = label("")
        self.install = QPushButton("Install engine")
        self.install.clicked.connect(self.install_engine)
        row.addWidget(self.engine_status, 1)
        row.addWidget(self.install)
        layout.addLayout(row)
        self.device.currentIndexChanged.connect(self.refresh)
        layout.addWidget(label("Speaker identification"))
        layout.addWidget(label("Hugging Face supplies the speaker model. Accept its access conditions with your account, then create a read token. Recordings are processed on this computer.", True))
        links = QHBoxLayout()
        for text, url in (("Open model access page", "https://huggingface.co/pyannote/speaker-diarization-community-1"),
                          ("Create a read token", "https://huggingface.co/settings/tokens")):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, url=url: QDesktopServices.openUrl(QUrl(url)))
            links.addWidget(button)
        layout.addLayout(links)
        self.token = QLineEdit(parent.token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Paste your Hugging Face read token")
        self.token.setAccessibleName("Hugging Face read token")
        layout.addWidget(self.token)
        self.remember = QCheckBox("Remember the token in the operating system credential store")
        self.remember.setChecked(parent.remember_token)
        layout.addWidget(self.remember)
        self.check = QPushButton("Check model access")
        self.check.clicked.connect(self.check_access)
        layout.addWidget(self.check)
        self.step_status = label("")
        layout.addWidget(self.step_status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(100)
        self.log.setMaximumBlockCount(250)
        self.log.setPlaceholderText("Installation and access-check messages appear here.")
        layout.addWidget(self.log, 1)
        self.copy_log = QPushButton("Copy installation log")
        self.copy_log.clicked.connect(self.copy_installation_log)
        log_actions = QHBoxLayout()
        log_actions.addWidget(self.copy_log, 1)
        help_button = QPushButton("Installation help")
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/emmachados/marcadores-de-paula/blob/main/docs/troubleshooting.md#engine-installation-on-windows")))
        log_actions.addWidget(help_button)
        layout.addLayout(log_actions)
        self.save_button = QPushButton("Save and close")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.refresh()

    def refresh(self):
        ready = engine_ready(self.device.currentData())
        self.engine_status.setText("Engine installed" if ready else "Engine not installed")
        self.install.setText("Repair engine" if ready else "Install engine")

    def install_engine(self):
        self.action = "install"
        self.install_device = self.device.currentData()
        self.log.clear()
        self.log_path = None
        self.recovered_python = None
        try:
            folder = runtime_root() / "setup-logs"
            folder.mkdir(parents=True, exist_ok=True)
            self.log_path = folder / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{self.install_device}.log"
            self.log_path.write_text("", encoding="utf-8")
            self.commands = setup_commands(self.install_device)
            self.install_marker = engine_dir(self.install_device) / "ready.json"
            self.install_marker.unlink(missing_ok=True)
        except (OSError, ValueError, ImportError) as exc:
            self.commands = []
            self.write_log("Could not prepare installation: " + str(exc))
            self.step_status.setText("Installation could not start. Copy the log for assistance.")
            self.busy(False)
            return
        self.step_number = 0
        self.step_total = len(self.commands)
        self.write_log(f"Marcadores de Paula {__version__}. Engine: {self.install_device}. Runtime folder: {runtime_root()}")
        self.write_log(f"Installation log: {self.log_path}")
        self.write_log("Installing the processing engine. Downloads and unpacking can take several minutes.")
        self.next_command()

    def check_access(self):
        if not self.token.text().strip():
            self.log.appendPlainText("Paste a read token first.")
            return
        if not engine_ready(self.device.currentData()):
            self.log.appendPlainText("Install the processing engine first.")
            return
        self.action = "access"
        self.commands = [[str(engine_python(self.device.currentData())), "-c",
                          "from huggingface_hub import hf_hub_download; hf_hub_download('pyannote/speaker-diarization-community-1', 'config.yaml'); print('Speaker model access confirmed.')"]]
        self.step_number = 0
        self.step_total = 1
        self.next_command()

    def next_command(self):
        if not self.commands:
            if self.action == "install":
                try:
                    self.install_marker.write_text(json.dumps(engine_record(self.install_device)), encoding="utf-8")
                except OSError as exc:
                    self.write_log("The engine passed its check, but its installation record could not be saved: " + str(exc))
                    self.step_status.setText("Installation record could not be saved. Copy the log for assistance.")
                    self.busy(False)
                    return
                self.write_log("Engine ready. Speech and speaker models download on first use.")
            self.step_status.setText("Engine installation complete." if self.action == "install" else "Speaker model access confirmed.")
            self.busy(False)
            self.refresh()
            return
        self.busy(True)
        command = self.commands.pop(0)
        self.step_output = ""
        self.step_number += 1
        self.step_started = time.monotonic()
        self.write_log(f"Step {self.step_number}/{self.step_total}: " + " ".join(command))
        self.update_step_status()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.setProcessEnvironment(child_environment(self.token.text().strip() if self.action == "access" else ""))
        self.process.setWorkingDirectory(str(runtime_root()))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        try:
            start_process(self.process, command)
        except OSError as exc:
            self.write_log(str(exc))
            self.process_error(QProcess.FailedToStart)

    def busy(self, value):
        for widget in (self.device, self.install, self.check, self.save_button, self.token, self.remember):
            widget.setEnabled(not value)
        self.progress.setVisible(value)
        self.progress.setRange(0, 0)
        self.heartbeat.start() if value else self.heartbeat.stop()
        if not value:
            self.refresh()

    def update_step_status(self):
        elapsed = max(0, int(time.monotonic() - self.step_started))
        self.step_status.setText(f"Step {self.step_number}/{self.step_total} · {elapsed // 60}:{elapsed % 60:02d} elapsed. Waiting for the installer to finish this step.")

    def write_log(self, text):
        token = self.token.text().strip()
        if token:
            text = text.replace(token, "[redacted]")
        self.log.appendPlainText(text.rstrip())
        if self.log_path:
            try:
                with self.log_path.open("a", encoding="utf-8") as stream:
                    stream.write(text.rstrip() + "\n")
            except OSError as exc:
                self.log_path = None
                self.log.appendPlainText("Could not save the installation log: " + str(exc))

    def copy_installation_log(self):
        try:
            text = self.log_path.read_text(encoding="utf-8") if self.log_path else self.log.toPlainText()
        except OSError:
            text = self.log.toPlainText()
        QApplication.clipboard().setText(text)

    def read_output(self):
        if self.process:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if text:
                self.step_output += text
                self.write_log(text)

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.write_log("Could not start setup: " + self.process.errorString())
            self.commands = []
            self.busy(False)
            self.step_status.setText("Installation could not start. Copy the log for assistance.")
            self.process.deleteLater()
            self.process = None

    def process_finished(self, code, status):
        self.read_output()
        self.process.deleteLater()
        self.process = None
        if code != 0 or status != QProcess.NormalExit:
            self.write_log(f"Step {self.step_number}/{self.step_total} failed. Exit code: {code}; status: {status.name}.")
            if self.action == "install" and self.step_number == 1 and status == QProcess.NormalExit:
                try:
                    recovery = python_link_recovery(self.step_output, self.install_marker.parent, self.commands)
                except OSError as exc:
                    recovery = None
                    self.write_log("Could not inspect the extracted Python installation: " + str(exc))
                if recovery:
                    self.recovered_python, self.commands = recovery
                    self.step_total = self.step_number + len(self.commands)
                    self.write_log("Windows blocked Python's version link. Checking the extracted interpreter directly: " + str(self.recovered_python))
                    self.next_command()
                    return
            self.commands = []
            advice = installation_advice(self.step_output, code)
            if advice:
                self.write_log(advice)
            self.step_status.setText("Installation failed. Copy the log for assistance, or select Install engine to retry." if self.action == "install" else "Model access check failed. See the log for details.")
            self.busy(False)
            return
        self.next_command()

    def save(self):
        parent = self.parent()
        token = self.token.text().strip()
        try:
            import keyring
            if self.remember.isChecked() and token:
                keyring.set_password("TranscriptDesk", "huggingface", token)
            elif parent.remember_token:
                try:
                    keyring.delete_password("TranscriptDesk", "huggingface")
                except keyring.errors.PasswordDeleteError:
                    pass
        except Exception as exc:
            self.log.appendPlainText("The credential store could not be updated. Uncheck Remember to use the token for this session. " + str(exc))
            return
        parent.token = token
        parent.remember_token = self.remember.isChecked()
        parent.device = self.device.currentData()
        parent.settings.setValue("device", parent.device)
        parent.settings.setValue("remember_token", parent.remember_token)
        parent.refresh_engine_status()
        self.accept()

    def reject(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            self.log.appendPlainText("Wait for this installation step to finish before closing.")
            return
        super().reject()
