"""Serial disk writes; the worker never accesses the live Qt document."""
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from threading import Lock

from PySide6.QtCore import QObject, Signal


class ReviewWriter(QObject):
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paula-review-save')
        self.guard = Lock()
        self.futures = set()
        self.outcomes = []

    def submit(self, folder, result, *, automatic, token):
        folder = Path(folder)

        def write():
            from transcript_desk.review_storage import save_checkpoint
            from transcript_desk.automatic_revisions import record_revision
            outcome = {'token': token, 'folder': str(folder), 'automatic': automatic,
                       'saved_at': result.get('metadata', {}).get('review_saved_at', ''),
                       'ok': False, 'error': '', 'revision_error': ''}
            try:
                save_checkpoint(folder, result, automatic=automatic)
                outcome['ok'] = True
            except Exception as exc:
                outcome['error'] = str(exc)
            if outcome['ok']:
                try:
                    record_revision(folder, result, force=not automatic)
                except Exception as exc:
                    outcome['revision_error'] = str(exc)
            with self.guard:
                self.outcomes.append(outcome)
            self.ready.emit()
            return outcome

        future = self.executor.submit(write)
        with self.guard:
            self.futures.add(future)
        return future

    def take_outcomes(self):
        with self.guard:
            outcomes, self.outcomes = self.outcomes, []
            self.futures = {future for future in self.futures if not future.done()}
        return outcomes

    def flush(self, timeout=120):
        with self.guard:
            pending = list(self.futures)
        if not pending:
            return True
        _, unfinished = wait(pending, timeout=timeout)
        return not unfinished

    def shutdown(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
