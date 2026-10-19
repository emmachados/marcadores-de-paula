"""Elapsed processing times, independent of progress percentages."""
from contextlib import contextmanager
from time import monotonic


@contextmanager
def timed_stage(metadata, emit, name, label):
    metadata['current_stage'] = name
    emit("progress", stage=label, stage_id=name, percent=None)
    started = monotonic()
    status = "failed"
    try:
        yield
        status = "complete"
    except Exception as exc:
        # Retain the observed failing stage while preserving the exception type.
        if not getattr(exc, 'processing_stage', None):
            exc.processing_stage = name
        raise
    finally:
        seconds = max(0.0, monotonic() - started)
        metadata.setdefault("processing_stages", {})[name] = {
            "seconds": seconds, "status": status,
        }
        emit("timing", stage=label, stage_id=name, seconds=seconds, status=status)
