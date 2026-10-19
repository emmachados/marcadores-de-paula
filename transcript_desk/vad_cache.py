"""Load a complete cached Silero repository without Torch Hub branch discovery."""
from pathlib import Path


def cached_silero_vad():
    import torch
    hub = Path(torch.hub.get_dir())
    required = ('hubconf.py', 'src/silero_vad/utils_vad.py', 'src/silero_vad/data/silero_vad.jit')
    for branch in ('main', 'master'):
        repository = hub / ('snakers4_silero-vad_' + branch)
        if not all((repository / name).is_file() and (repository / name).stat().st_size > 0 for name in required):
            continue
        from whisperx.vads.silero import Silero
        from whisperx.vads.vad import Vad

        class CachedSilero(Silero):
            def __init__(self):
                # Preserve WhisperX 3.8.6's Silero settings and inherited audio,
                # timestamp and chunk-merging methods. Its normal constructor
                # asks GitHub for the default branch even when files are cached.
                Vad.__init__(self, 0.500)
                self.vad_onset = 0.500
                self.chunk_size = 30
                self.vad_pipeline, utils = torch.hub.load(
                    str(repository), 'silero_vad', source='local', onnx=False)
                self.get_speech_timestamps, _, self.read_audio, _, _ = utils

        return CachedSilero()
    # WhisperX still performs the first download when no cache exists.
    return None
