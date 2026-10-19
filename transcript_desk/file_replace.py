"""Atomic file replacement that tolerates brief Windows sharing violations."""
import os
import time

# Windows refuses replacement while another process, such as a sync client or
# virus scanner, holds the target open. These holds usually last milliseconds.
RETRY = os.name == 'nt'
DELAYS = (.05, .1, .2, .4, .8)


def replace_file(source, target):
    for delay in DELAYS if RETRY else ():
        try:
            return os.replace(source, target)
        except PermissionError:
            time.sleep(delay)
    return os.replace(source, target)
