"""Exclusive write ownership of a review folder across application instances."""
from pathlib import Path

from PySide6.QtCore import QLockFile


class ProjectLock:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.lock = QLockFile(str(self.folder / '.paula-review.lock'))
        # A live process retains ownership regardless of editing-session length.
        # Qt still identifies locks belonging to processes that have exited.
        self.lock.setStaleLockTime(0)
        self.owned = False

    def acquire(self):
        if self.owned:
            return True
        self.owned = self.lock.tryLock(0)
        return self.owned

    @property
    def message(self):
        if self.owned:
            return ''
        if self.lock.error() == QLockFile.PermissionError:
            return 'The project folder is not writable. The recording can be opened for reading.'
        return 'Another application instance owns this project. Close it there before editing this recording.'

    def release(self):
        if self.owned:
            self.lock.unlock()
            self.owned = False

    def __enter__(self):
        if not self.acquire():
            raise OSError(self.message)
        return self

    def __exit__(self, *_):
        self.release()
