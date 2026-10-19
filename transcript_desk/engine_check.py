"""Verify installed versions and imports before Setup marks an engine ready."""
from importlib.metadata import version
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse


def locked_versions(path):
    versions = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        match = re.match(r'^([a-zA-Z0-9_.-]+)==([^ ;\\]+)', line)
        if match:
            versions[match[1]] = match[2]
        elif re.match(r'^[a-zA-Z0-9_.-]+ @ ', line):
            name, url = line.split(' @ ', 1)
            filename = unquote(urlparse(url.split()[0]).path).rsplit('/', 1)[-1]
            versions[name] = filename.split('-')[1]
    if not versions:
        raise ValueError('The engine dependency lock is empty.')
    return versions


def check(path=None):
    expected = locked_versions(path) if path else {'whisperx': '3.8.6'}
    for name, wanted in expected.items():
        actual = version(name)
        if actual != wanted:
            raise RuntimeError(f'{name}: expected {wanted}, found {actual}')
    from transcript_desk.media_dependencies import prepare_media_libraries
    prepare_media_libraries()
    import torch
    import whisperx
    import yt_dlp
    from whisperx.diarize import DiarizationPipeline
    if sys.platform == 'darwin':
        # pyannote only warns about a broken decoder on import. Decode real PCM
        # before declaring a Mac engine ready, so that warning cannot pass setup.
        import tempfile
        import wave
        from torchcodec.decoders import AudioDecoder
        with tempfile.TemporaryDirectory(prefix='paula-engine-check-') as directory:
            audio = Path(directory) / 'test.wav'
            with wave.open(str(audio), 'wb') as stream:
                stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0\0' * 1600)
            samples = AudioDecoder(str(audio)).get_all_samples()
            if samples.sample_rate != 16000 or samples.data.shape[-1] != 1600:
                raise RuntimeError('The processing engine failed its audio decoding check.')
    print(f'Engine imports verified; {len(expected)} package versions checked. PyTorch {torch.__version__}.')


if __name__ == '__main__':
    check(sys.argv[1] if len(sys.argv) > 1 else None)
