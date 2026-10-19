"""Bounded local diagnostics without transcript text, exception messages or tokens."""
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import sys
import traceback
import tempfile
import os
import zipfile

_active = None


def exception_record(kind, traceback_object):
    return {'exception': kind.__name__, 'frames': [
        {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
        for frame in traceback.extract_tb(traceback_object)]}


class Diagnostics:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger('transcript_desk.diagnostics')
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(self.folder / 'application.log', maxBytes=1_000_000,
                                      backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(handler)
        fault_path = self.folder / 'native-fault.log'
        if fault_path.exists() and fault_path.stat().st_size > 1_000_000:
            fault_path.replace(self.folder / 'native-fault.previous.log')
        self.fault_stream = fault_path.open('a', encoding='utf-8')
        faulthandler.enable(file=self.fault_stream, all_threads=True)
        self.previous_hook = sys.excepthook
        sys.excepthook = self.exception
        self.notify = None
        from PySide6.QtCore import qInstallMessageHandler, qVersion
        self.previous_qt_handler = qInstallMessageHandler(self.qt_message)
        from transcript_desk import __version__
        self.logger.info(json.dumps({'event': 'startup', 'version': __version__,
            'os': platform.system(), 'os_version': platform.release(),
            'architecture': platform.machine(), 'python': platform.python_version(), 'qt': qVersion()}))

    def exception(self, kind, value, tb):
        self.logger.error(json.dumps(exception_record(kind, tb)))
        if self.notify is not None:
            self.notify()
        self.previous_hook(kind, value, tb)

    def qt_message(self, kind, context, message):
        # Qt error messages can contain media paths or URLs. Store only severity
        # and category; native stack traces remain in the separate fault file.
        self.logger.info(json.dumps({'event': 'qt', 'severity': str(kind),
                                     'category': context.category or ''}))
        if self.previous_qt_handler is not None:
            self.previous_qt_handler(kind, context, message)


def install_diagnostics(folder=None):
    global _active
    if _active is not None:
        return _active
    try:
        from transcript_desk.runtime import data_root
        _active = Diagnostics(Path(folder) if folder is not None else data_root() / 'diagnostics')
    except OSError:
        # A diagnostics destination must never prevent recovery of a project.
        return None
    return _active


def record_processing_issue(issue):
    if _active is not None:
        from transcript_desk.processing_errors import diagnostic_issue_record
        _active.logger.info(json.dumps(diagnostic_issue_record(issue)))


def export_diagnostics(destination):
    """Export only the allowlisted local application diagnostics, bounded in size."""
    from transcript_desk.runtime import data_root
    folder = _active.folder if _active is not None else data_root() / 'diagnostics'
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.paula-diagnostics-', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('diagnostics-info.json', json.dumps({
                'format': 'paula-local-diagnostics', 'version': 1,
                'contents': 'Application versions, exception stack locations and native fault stacks. '
                            'No transcript, project files, queue or credentials are included.'}))
            for name in ('application.log', 'application.log.1', 'application.log.2',
                         'native-fault.log', 'native-fault.previous.log'):
                path = folder / name
                if not path.is_file() or path.is_symlink():
                    continue
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 1_000_000))
                    archive.writestr(name, stream.read(1_000_000))
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
