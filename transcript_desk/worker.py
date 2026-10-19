"""Isolated job process. Stdout carries newline-delimited JSON events."""
from __future__ import annotations

import contextlib
import copy
import gc
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from transcript_desk import __version__
from transcript_desk.exports import export_result, speaker_turns
from transcript_desk.processing import timed_stage
from transcript_desk.processing_errors import issue_from_exception, issue_text
from transcript_desk.vad_cache import cached_silero_vad
from transcript_desk.checkpoints import Checkpoints, file_hash
from transcript_desk.speaker_assignment import assign_speakers, assign_single_speaker, needs_speaker_model

PROTOCOL = sys.stdout
DLL_HANDLES = []


def emit(kind, **values):
    print(json.dumps({"event": kind, **values}, ensure_ascii=False), file=PROTOCOL, flush=True)


def atomic_json(path: Path, data: dict):
    try:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except Exception as exc:
        exc.processing_stage = 'output_saving'
        raise


def validate(config):
    source = config.get("source", "").strip()
    if config.get("source_type") == "url":
        if urlparse(source).scheme not in ("https", "http") or not urlparse(source).netloc:
            raise ValueError("Enter a complete http:// or https:// video link.")
    elif not Path(source).is_file():
        raise ValueError("The selected media file no longer exists.")
    if not any(config.get(k) for k in ("transcribe", "save_audio", "save_video")):
        raise ValueError("Choose a transcript, audio or video output.")
    if config.get("model") == "custom":
        from transcript_desk.model_pack import inspect_pack
        inspect_pack(config.get("custom_model_path") or "")
        if config.get("language") != "es":
            raise ValueError("The experimental corpus model requires Spanish.")
    elif config.get("model") not in ("large-v3", "large-v3-turbo"):
        raise ValueError("Choose large-v3 or large-v3-turbo.")
    if config.get("device") not in ("cpu", "cuda"):
        raise ValueError("Unknown processing device.")


def acquire(config, work: Path):
    if config["source_type"] == "file":
        source = Path(config["source"])
        return source, source.stem
    import yt_dlp
    last = [-1]

    def hook(data):
        if data["status"] == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            percent = int(data.get("downloaded_bytes", 0) * 100 / total) if total else None
            if percent != last[0]:
                emit("progress", stage="Downloading media", percent=percent)
                last[0] = percent

    class QuietLogger:
        def debug(self, message):
            pass

        def warning(self, message):
            emit("warning", message=str(message))

        def error(self, message):
            pass  # The raised DownloadError is reported once at the process boundary.

    options = {
        "format": "bestvideo*+bestaudio/best" if config["save_video"] else "bestaudio/best",
        "outtmpl": str(work / "source.%(ext)s"), "noplaylist": True,
        "progress_hooks": [hook], "logger": QuietLogger(), "quiet": True,
        "retries": 3, "socket_timeout": 30, "windowsfilenames": True,
        "ffmpeg_location": str(Path(config["ffmpeg"]).parent),
    }
    if config.get("deno"):
        options["js_runtimes"] = {"deno": {"path": config["deno"]}}
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(config["source"], download=True)
        if not info or info.get("_type") in ("playlist", "multi_video"):
            raise ValueError("This link is a playlist. Add the individual video links, one per line.")
        path = Path(info.get("filepath") or downloader.prepare_filename(info))
        if not path.exists():
            candidates = [p for p in work.glob("source.*") if p.suffix not in (".part", ".ytdl")]
            if len(candidates) != 1:
                raise RuntimeError("The download finished but its media file could not be located.")
            path = candidates[0]
        return path, info.get("title", "Recording")


def convert(ffmpeg, source: Path, target: Path, analysis=False):
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn"]
    if analysis:
        command += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
    else:
        command += ["-c:a", "flac"]
    command.append(str(target))
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise RuntimeError("The media could not be decoded. " + completed.stderr[-1600:])


def inspect_media(config, source):
    probe = Path(config["ffmpeg"]).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    command = str(probe) if probe.exists() else shutil.which("ffprobe")
    if not command:
        raise RuntimeError("FFprobe is missing. Reinstall the desktop application.")
    completed = subprocess.run([command, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise RuntimeError("The media file could not be inspected: " + completed.stderr[-1000:])
    info = json.loads(completed.stdout)
    if config["save_video"] and not any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in info["streams"]):
        raise ValueError("This recording contains no video track. Add it again with only Audio and/or Transcript selected.")
    digest = hashlib.sha256()
    with source.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "streams": info.get("streams", []), "format": info.get("format", {})}


def prepare_torch_libraries():
    if sys.platform != "win32":
        return
    # CTranslate2 needs CUDA DLLs installed with the selected PyTorch wheel.
    import importlib.util
    spec = importlib.util.find_spec("torch")
    if spec and spec.origin:
        lib = Path(spec.origin).parent / "lib"
        os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
        DLL_HANDLES.append(os.add_dll_directory(str(lib)))


def warn_issue(metadata, exc, stage):
    issue = issue_from_exception(exc, stage, partial=True)
    message = issue_text(issue)
    metadata.setdefault("warnings", []).append(message)
    metadata.setdefault("processing_issues", []).append(issue)
    emit("warning", message=message, issue=issue, partial=True)


def transcribe(config, audio_path: Path, metadata: dict, output: Path, checkpoints=None):
    with timed_stage(metadata, emit, "engine_loading", "Loading the processing engine"):
        prepare_torch_libraries()
        import torch
        from transcript_desk.media_dependencies import prepare_media_libraries
        prepare_media_libraries()
        import whisperx

    device = config["device"]
    with timed_stage(metadata, emit, "audio_loading", "Loading transcription audio"):
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("NVIDIA processing is unavailable. Select CPU in Setup and install the CPU engine, or check the NVIDIA driver.")
        audio = whisperx.load_audio(str(audio_path))
    metadata["duration"] = len(audio) / 16000
    versions = {p: importlib.metadata.version(p) for p in ("whisperx", "faster-whisper", "torch", "pyannote.audio", "yt-dlp")}
    if checkpoints and checkpoints.has('transcribed') and metadata.get('versions') != versions:
        raise ValueError('The processing engine changed since this checkpoint. Use Retry from start to create a separate attempt.')
    metadata["versions"] = versions
    # Keep the decoder's repetition settings neutral. Whisper can still omit
    # words, so transcripts must be checked against the audio.
    asr_options = {"repetition_penalty": 1.0, "no_repeat_ngram_size": 0, "suppress_numerals": False}
    metadata["transcription_policy"] = {"wording_cleanup": False, "asr_options": asr_options,
        "limitation": "Automatic recognition may omit fillers, repetitions or false starts."}
    model_source = config["model"]
    if model_source == "custom":
        from transcript_desk.model_pack import inspect_pack
        folder, pack = inspect_pack(config["custom_model_path"])
        model_source = str(folder)
        metadata["experimental_model"] = pack
    if checkpoints and checkpoints.has("transcribed"):
        result = checkpoints.data["result"]
        result["metadata"] = metadata
        emit("progress", stage="Resuming saved transcript", percent=None)
    else:
        with timed_stage(metadata, emit, "speech_model_loading", "Loading speech model; the first use downloads its files"):
            cached_vad = cached_silero_vad()
            model = whisperx.load_model(model_source, device,
                                        compute_type="int8" if device == "cpu" else "float16",
                                        language=config.get("language") or None,
                                        vad_method="silero", vad_model=cached_vad, asr_options=asr_options)
        with timed_stage(metadata, emit, "transcription", "Transcribing speech"):
            result = model.transcribe(audio, batch_size=1 if device == "cpu" else 4,
                                      progress_callback=lambda pct: emit("progress", stage="Transcribing speech", stage_id="transcription", percent=pct))
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()
        metadata["language"] = result.get("language")
        result["metadata"] = metadata
        result["speaker_names"] = {}
        atomic_json(output / "result.json", result)
        atomic_json(output / "asr-original.json", result)
        if checkpoints:
            checkpoints.save("transcribed", metadata, result)
    if not result.get("segments"):
        emit("warning", message="No speech was detected. An empty transcript has been saved.")
        metadata["warnings"].append("No speech detected.")
        return result
    if not checkpoints or not checkpoints.has("aligned"):
        try:
            with timed_stage(metadata, emit, "alignment", "Loading alignment model and aligning words"):
                align_model, align_metadata = whisperx.load_align_model(language_code=result["language"], device=device)
                aligned = whisperx.align(result["segments"], align_model, align_metadata, audio, device, return_char_alignments=False)
            result.update(aligned)
            metadata["alignment"] = "complete"
            del align_model
            gc.collect()
            if device == "cuda":
                torch.cuda.empty_cache()
        except Exception as exc:
            metadata["alignment"] = "unavailable"
            warn_issue(metadata, exc, "alignment")
        atomic_json(output / "result.json", result)
        if checkpoints and metadata.get("alignment") == "complete":
            checkpoints.save("aligned", metadata, result)
    if checkpoints and checkpoints.has("diarized"):
        return result
    if config["diarize"]:
        # Assignment may modify the result in place. Publish it only after all
        # speaker stages succeed, retaining the recognized words on failure.
        speaker_result = copy.deepcopy(result)
        speaker_result["metadata"] = metadata
        try:
            if config.get("speakers") == 1:
                with timed_stage(metadata, emit, "speaker_assignment", "Assigning the single speaker specified for this recording"):
                    speaker_result = assign_single_speaker(speaker_result)
            else:
                with timed_stage(metadata, emit, "speaker_model_loading", "Loading speaker model; the first use downloads its files"):
                    from whisperx.diarize import DiarizationPipeline
                    diarizer = DiarizationPipeline(token=os.environ.get("HF_TOKEN") or None, device=device)
                with timed_stage(metadata, emit, "diarization", "Identifying speakers"):
                    speakers = diarizer(audio, num_speakers=config.get("speakers") or None,
                                        progress_callback=lambda pct: emit("progress", stage="Identifying speakers", stage_id="diarization", percent=pct))
                with timed_stage(metadata, emit, "speaker_assignment", "Assigning speakers to transcript words"):
                    intervals = [(float(r.start), float(r.end), r.speaker) for r in speakers.itertuples()]
                    speaker_result = assign_speakers(intervals, speaker_result)
                    speaker_result["original_segments"] = speaker_result["segments"]
                    speaker_result["segments"] = speaker_turns(speaker_result)
                    metadata["diarization"] = "complete"
                    metadata["speaker_intervals"] = [{"start": start, "end": end, "speaker": speaker}
                                                     for start, end, speaker in intervals]
            result = speaker_result
        except Exception as exc:
            metadata["diarization"] = "failed"
            metadata.pop("speaker_intervals", None)
            warn_issue(metadata, exc, getattr(exc, "processing_stage", "speaker_assignment"))
    result["metadata"] = metadata
    if checkpoints and metadata.get("alignment") != "unavailable" and metadata.get("diarization") != "failed":
        checkpoints.save("diarized", metadata, result)
    return result


def run(config):
    output = Path(config["output"])
    metadata = {}
    with timed_stage(metadata, emit, "output_saving", "Preparing the recording output folder"):
        output.mkdir(parents=True, exist_ok=True)
    with timed_stage(metadata, emit, "checkpoint", "Checking saved processing files"):
        checkpoints = Checkpoints(config)
    # A retained analysis file permits resume even if the original file moved.
    if not checkpoints.has("prepared"):
        with timed_stage(metadata, emit, "validation", "Checking recording settings"):
            validate(config)
    os.environ["PATH"] = str(Path(config["ffmpeg"]).parent) + os.pathsep + os.environ.get("PATH", "")
    if checkpoints.has("prepared"):
        metadata = checkpoints.data["metadata"]
        emit("progress", stage="Checking saved processing files", percent=None)
        with timed_stage(metadata, emit, "checkpoint", "Checking saved processing audio"):
            analysis = checkpoints.validate_audio() if config["transcribe"] else None
        with timed_stage(metadata, emit, "checkpoint", "Checking retained media"):
            for name in metadata.get("retained_files", []):
                path = output / name
                if (not path.is_file() or file_hash(path) != metadata.get('retained_sha256', {}).get(name)):
                    raise ValueError("A retained media file is missing or changed. Use Retry from start to create a separate attempt.")
        metadata["resume_count"] = metadata.get("resume_count", 0) + 1
    else:
        metadata = {"source": config["source"], "source_type": config["source_type"],
                    "created_at": datetime.now(timezone.utc).isoformat(), "model": config["model"],
                    "device": config["device"], "requested_speakers": config.get("speakers"),
                    "diarization": ("pending" if config["diarize"] else "disabled") if config["transcribe"] else "not_requested",
                    "warnings": [], "application_version": __version__,
                    "settings": {k: v for k, v in config.items() if k not in ("ffmpeg", "deno")}, "retained_files": []}
        emit("progress", stage="Preparing media", percent=None)
        with timed_stage(metadata, emit, "media_acquisition", "Preparing media"):
            media, title = acquire(config, checkpoints.folder)
        metadata["title"] = title
        with timed_stage(metadata, emit, "media_inspection", "Inspecting media"):
            metadata["input_media"] = inspect_media(config, media)
        if config["save_video"]:
            destination = output / ("video" + media.suffix)
            with timed_stage(metadata, emit, "output_saving", "Saving the video"):
                shutil.copy2(media, destination)
            metadata["media_path"] = str(destination)
            metadata["retained_files"].append(destination.name)
        if config["save_audio"] or config["transcribe"]:
            emit("progress", stage="Saving lossless audio", percent=None)
            destination = output / ("audio.flac" if config["save_audio"] else "review-audio.flac")
            with timed_stage(metadata, emit, "media_conversion", "Saving lossless audio"):
                convert(config["ffmpeg"], media, destination)
            metadata["media_path"] = str(destination)
            metadata["retained_files"].append(destination.name)
        if config["source_type"] == "file" and not metadata.get("media_path"):
            metadata["media_path"] = str(media.resolve())
        analysis = checkpoints.folder / "analysis.wav"
        if config["transcribe"]:
            emit("progress", stage="Preparing audio for transcription", percent=None)
            with timed_stage(metadata, emit, "audio_preparation", "Preparing audio for transcription"):
                convert(config["ffmpeg"], media, analysis, analysis=True)
            metadata["analysis_sha256"] = file_hash(analysis)
        metadata['retained_sha256'] = {name: file_hash(output / name) for name in metadata['retained_files']}
        checkpoints.save("prepared", metadata)
    atomic_json(output / "source.json", metadata)
    if config["transcribe"]:
        try:
            result = transcribe(config, analysis, metadata, output, checkpoints)
        finally:
            atomic_json(output / "source.json", metadata)
        atomic_json(output / "result.json", result)
        emit("progress", stage="Writing transcript files", percent=None)
        if not config.get("review_before_export", False):
            with timed_stage(metadata, emit, "export", "Writing transcript files"):
                export_result(result, output, config["formats"])
    atomic_json(output / "source.json", metadata)
    # Publish completion before removing recoverable intermediates. If interrupted
    # during cleanup, the finished transcript and media remain available.
    emit("complete", output=str(output), title=metadata["title"],
         warnings=metadata.get("warnings", []), issues=metadata.get("processing_issues", []))
    if metadata.get("alignment") != "unavailable" and metadata.get("diarization") != "failed":
        shutil.rmtree(checkpoints.folder, ignore_errors=True)


def main(path=None):
    for stream in (PROTOCOL, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    config = json.loads(Path(path or sys.argv[1]).read_text(encoding="utf-8"))
    try:
        # Dependencies print diagnostic text; keep it outside the event protocol.
        with contextlib.redirect_stdout(sys.stderr):
            run(config)
        return 0
    except Exception as exc:
        result_path = Path(config["output"]) / "result.json"
        issue = issue_from_exception(exc, partial=result_path.exists())
        message = issue_text(issue)
        if result_path.exists():
            try:
                partial = json.loads(result_path.read_text(encoding="utf-8"))
                source_metadata = json.loads((result_path.parent / "source.json").read_text(encoding="utf-8"))
                partial.setdefault("metadata", {}).update(source_metadata)
                partial.setdefault("metadata", {})["failure"] = message
                partial["metadata"]["failure_issue"] = issue
                if partial["metadata"].get("diarization") == "pending":
                    partial["metadata"]["diarization"] = "failed"
                atomic_json(result_path, partial)
            except (OSError, ValueError):
                pass
        emit("error", message=message, partial=result_path.exists(), issue=issue)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
