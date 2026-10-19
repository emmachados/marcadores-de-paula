"""Make bundled macOS FFmpeg libraries available to the processing engine."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

_handles = []


def prepare_media_libraries():
    if sys.platform != 'darwin' or _handles:
        return
    root = Path(os.environ.get('TRANSCRIPT_DESK_FFMPEG_LIB', ''))
    if not os.environ.get('TRANSCRIPT_DESK_FFMPEG_LIB') or not root.is_dir():
        return
    # The install names match TorchCodec's @rpath names. Loading in dependency
    # order lets dyld reuse these images without modifying the installed engine.
    for stem in ('libavutil', 'libswresample', 'libswscale', 'libavcodec', 'libavformat', 'libavfilter', 'libavdevice'):
        candidates = sorted(root.glob(stem + '.*.dylib'))
        if not candidates:
            raise RuntimeError(f'The bundled decoder library {stem} is missing. Reinstall the application.')
        _handles.append(ctypes.CDLL(str(candidates[0]), mode=ctypes.RTLD_GLOBAL))
