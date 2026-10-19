"""One GUI queue owner per application-data folder; workers use no instance lock."""
from pathlib import Path

from PySide6.QtCore import QLockFile


class ApplicationLock:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.lock = QLockFile(str(self.folder / '.paula-application.lock'))
        self.lock.setStaleLockTime(0)
        self.owned = False

    def acquire(self):
        if not self.owned:
            self.owned = self.lock.tryLock(0)
        return self.owned

    @property
    def message(self):
        if self.lock.error() == QLockFile.LockFailedError:
            return ('The application is already open for this data folder. '
                    'Use that window or close it before opening another.')
        return ('The application-data folder could not be locked. '
                'Check its write permissions and free disk space before restarting.')

    def release(self):
        if self.owned:
            self.lock.unlock()
            self.owned = False
