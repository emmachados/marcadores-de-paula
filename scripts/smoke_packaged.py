"""Run the frozen download worker against generated local media."""
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

root = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    audio = work / "test audio.wav"
    with wave.open(str(audio), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\0\0" * 16000)
    job = {"source": str(audio), "source_type": "file", "model": "large-v3-turbo", "device": "cpu",
           "output": str(work / "output"), "ffmpeg": str(root / "_internal/tools/ffmpeg.exe"),
           "save_audio": True, "save_video": False, "transcribe": False, "diarize": False, "formats": []}
    config = work / "job.json"
    config.write_text(json.dumps(job), encoding="utf-8")
    process = subprocess.run([str(root / "_internal/worker/TranscriptWorker.exe"), str(config)],
                             capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert process.returncode == 0, process.stdout + process.stderr
    assert (work / "output/audio.flac").read_bytes().startswith(b"fLaC")
    assert json.loads(process.stdout.splitlines()[-1])["event"] == "complete"
    # Exercise the frozen worker's structured failure protocol without engines,
    # internet access, credentials or user recordings.
    job['source'] = str(work / 'missing.wav')
    job['output'] = str(work / 'failed-output')
    config.write_text(json.dumps(job), encoding='utf-8')
    failed = subprocess.run([str(root / '_internal/worker/TranscriptWorker.exe'), str(config)],
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    event = json.loads(failed.stdout.splitlines()[-1])
    assert event['event'] == 'error'
    assert event['issue']['stage'] == 'validation'
    assert event['issue']['category'] == 'invalid_input'
    assert event['partial'] is False
print("Packaged worker, FFmpeg and structured failure protocol verified.")
