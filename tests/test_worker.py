import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from transcript_desk.worker import download_format, validate


def make_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)


def test_real_download_only_worker_and_ffmpeg(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    source = tmp_path / "input café with spaces.wav"
    make_wav(source)
    output = tmp_path / "output"
    config = {"source": str(source), "source_type": "file", "model": "large-v3-turbo", "device": "cpu",
              "output": str(output), "ffmpeg": ffmpeg, "transcribe": False, "diarize": False,
              "save_video": False, "save_audio": True, "formats": []}
    config_path = tmp_path / "job.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    completed = subprocess.run([sys.executable, "-m", "transcript_desk.worker", str(config_path)], capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (output / "audio.flac").read_bytes().startswith(b"fLaC")
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[-1]["event"] == "complete"
    assert events[-1]["title"] == source.stem
    assert json.loads((output / "source.json").read_text(encoding="utf-8"))["source"] == str(source)
    assert not list(output.glob(".work-*"))


def test_invalid_url_rejected_before_processing():
    with pytest.raises(ValueError, match="complete"):
        validate({"source_type": "url", "source": "file:///etc/passwd"})


@pytest.mark.parametrize("url", ["https://www.tiktok.com/@user/video/1", "https://vm.tiktok.com/ZMabc/"])
@pytest.mark.parametrize("save_video", [False, True])
def test_tiktok_uses_the_audio_inside_the_video(url, save_video):
    assert download_format({"source": url, "save_video": save_video}) == "best"


def test_other_sites_keep_the_audio_only_format():
    assert download_format({"source": "https://www.youtube.com/watch?v=x", "save_video": False}) == "bestaudio/best"
    assert download_format({"source": "https://nottiktok.com/v/1", "save_video": True}) == "bestvideo*+bestaudio/best"


def test_alignment_and_diarization_run_with_the_threads_silero_takes_away(tmp_path, monkeypatch):
    from types import ModuleType, SimpleNamespace
    import transcript_desk.worker as worker
    threads, seen = [6], {}
    def load_model(*a, **kw):
        threads[0] = 1  # Silero's model module calls torch.set_num_threads(1) on import.
        return SimpleNamespace(transcribe=lambda *a, **kw: {'language': 'es', 'segments': [{'start': 0, 'end': 1, 'text': 'bueno'}]})
    def align(segments, *a, **kw):
        seen['alignment'] = threads[0]
        return {'segments': segments}
    def diarize(*a, **kw):
        seen['diarization'] = threads[0]
        return SimpleNamespace(itertuples=lambda: iter([SimpleNamespace(start=0, end=1, speaker='A')]))
    whisperx = ModuleType('whisperx')
    whisperx.load_audio = lambda _: [0] * 16000
    whisperx.load_model = load_model
    whisperx.load_align_model = lambda **kw: (object(), {})
    whisperx.align = align
    module = ModuleType('whisperx.diarize')
    module.DiarizationPipeline = lambda *a, **kw: diarize
    monkeypatch.setitem(sys.modules, 'whisperx', whisperx)
    monkeypatch.setitem(sys.modules, 'whisperx.diarize', module)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(get_num_threads=lambda: threads[0],
                                                              set_num_threads=lambda n: threads.__setitem__(0, n)))
    monkeypatch.setattr(worker, 'prepare_torch_libraries', lambda: None)
    monkeypatch.setattr(worker, 'cached_silero_vad', lambda: None)
    monkeypatch.setattr(worker.importlib.metadata, 'version', lambda _: 'test')
    monkeypatch.setattr(worker, 'emit', lambda *a, **kw: None)
    config = {'device': 'cpu', 'model': 'large-v3-turbo', 'diarize': True, 'speakers': 0}
    worker.transcribe(config, tmp_path / 'unused.wav', {'warnings': []}, tmp_path)
    assert seen == {'alignment': 6, 'diarization': 6}


def test_worker_ignores_modules_in_the_working_folder(tmp_path, qapp, monkeypatch):
    from transcript_desk.runtime import child_environment
    monkeypatch.setenv('TRANSCRIPT_DESK_DATA', str(tmp_path / 'data'))
    (tmp_path / 'csv.py').write_text('raise SystemExit(3)')
    config = {'source': str(tmp_path / 'missing.wav'), 'source_type': 'file', 'output': str(tmp_path / 'output')}
    path = tmp_path / 'job.json'
    path.write_text(json.dumps(config))
    environment = dict(item.split('=', 1) for item in child_environment().toStringList())
    child = subprocess.run([sys.executable, '-m', 'transcript_desk.worker', str(path)], cwd=tmp_path,
                           env=environment, capture_output=True, text=True, timeout=30)
    assert child.returncode == 1, child.stderr
    assert json.loads(child.stdout.splitlines()[-1])['issue']['stage'] == 'validation'
