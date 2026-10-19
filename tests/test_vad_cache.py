import sys
from types import ModuleType, SimpleNamespace

import pytest

from transcript_desk.vad_cache import cached_silero_vad


def fake_vad_api(monkeypatch, tmp_path):
    calls = []
    vad = ModuleType('whisperx.vads.vad')
    class Vad:
        def __init__(self, onset):
            self.validated_onset = onset
    vad.Vad = Vad
    silero = ModuleType('whisperx.vads.silero')
    class Silero(Vad):
        def __init__(self, **kwargs):
            raise AssertionError('Upstream constructor would probe GitHub')
        def __call__(self, audio):
            return self.get_speech_timestamps(audio, self.vad_pipeline, threshold=self.vad_onset)
    silero.Silero = Silero
    def load(repository, model, *, source='github', onnx=False):
        if source != 'local':
            raise AssertionError('Network model loading is prohibited in this cached test')
        calls.append((repository, model, source, onnx))
        return 'cached-model', (lambda audio, model, threshold: [audio, model, threshold], None, 'reader', None, None)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(hub=SimpleNamespace(get_dir=lambda: str(tmp_path), load=load)))
    monkeypatch.setitem(sys.modules, 'whisperx.vads.silero', silero)
    monkeypatch.setitem(sys.modules, 'whisperx.vads.vad', vad)
    return calls, Silero


def make_repository(root, branch='master'):
    folder = root / ('snakers4_silero-vad_' + branch)
    for name in ('hubconf.py', 'src/silero_vad/utils_vad.py', 'src/silero_vad/data/silero_vad.jit'):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'cached fixture')
    return folder


def test_cached_vad_uses_local_source_without_upstream_network_constructor(tmp_path, monkeypatch):
    calls, Silero = fake_vad_api(monkeypatch, tmp_path)
    repository = make_repository(tmp_path)
    vad = cached_silero_vad()
    assert isinstance(vad, Silero)
    assert calls == [(str(repository), 'silero_vad', 'local', False)]
    assert vad.validated_onset == vad.vad_onset == 0.500
    assert vad.chunk_size == 30
    assert vad.read_audio == 'reader'
    assert vad('synthetic audio') == ['synthetic audio', 'cached-model', 0.500]


@pytest.mark.parametrize('incomplete', [False, True])
def test_missing_or_incomplete_cache_preserves_whisperx_first_download_path(tmp_path, monkeypatch, incomplete):
    calls, _ = fake_vad_api(monkeypatch, tmp_path)
    if incomplete:
        folder = make_repository(tmp_path)
        (folder / 'src/silero_vad/data/silero_vad.jit').write_bytes(b'')
    assert cached_silero_vad() is None
    assert calls == []
