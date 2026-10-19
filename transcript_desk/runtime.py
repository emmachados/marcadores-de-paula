from __future__ import annotations

import json
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from transcript_desk.file_replace import replace_file

ENGINE_VERSION = "3.8.6"


def default_output_folder() -> Path:
    """Use the Documents folder Windows reports, which OneDrive backup may redirect."""
    legacy = Path.home() / "Documents" / "Marcadores de Paula"
    documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    if legacy.exists() or not documents:
        return legacy  # Earlier versions created this folder; keep new work beside it.
    return Path(documents) / "Marcadores de Paula"


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def data_root() -> Path:
    override = os.environ.get("TRANSCRIPT_DESK_DATA")
    root = Path(override) if override else Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


def tool_path(name):
    bundled = resource_root() / "tools" / (name + (".exe" if os.name == "nt" else ""))
    return str(bundled) if bundled.exists() else shutil.which(name)


def runtime_root() -> Path:
    """Keep executable runtimes out of Windows roaming/cloud profile paths."""
    override = os.environ.get("TRANSCRIPT_DESK_RUNTIME") or os.environ.get("TRANSCRIPT_DESK_DATA")
    root = Path(override) if override else Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


def engine_dir(device):
    local = runtime_root() / f"engine-{device}"
    legacy = data_root() / f"engine-{device}"
    # Retain a completed older installation; incomplete attempts use the local path.
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    if (not os.environ.get("TRANSCRIPT_DESK_RUNTIME") and not local.exists()
            and legacy != local and (legacy / executable).is_file()):
        try:
            if json.loads((legacy / "ready.json").read_text())["whisperx"] == ENGINE_VERSION:
                return legacy
        except (OSError, ValueError, KeyError):
            pass
    return local


def engine_python(device):
    root = engine_dir(device)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def engine_ready(device):
    marker = engine_dir(device) / "ready.json"
    try:
        record = json.loads(marker.read_text())
        if not engine_python(device).is_file() or record["whisperx"] != ENGINE_VERSION:
            return False
        # Older installations have no digest; new installations must match their lock.
        if record.get("requirements_sha256"):
            return record["requirements_sha256"] == engine_record(device).get("requirements_sha256")
        return True
    except (OSError, ValueError, KeyError):
        return False


def engine_lock(device):
    if sys.platform == 'win32':
        return resource_root() / f'requirements-engine-windows-{device}.txt'
    if sys.platform == 'darwin':
        if device != 'cpu':
            raise ValueError('The Mac processing engine uses CPU. NVIDIA CUDA is unavailable on macOS.')
        if platform.machine().lower() not in ('arm64', 'aarch64'):
            raise ValueError('This WhisperX engine requires an Apple silicon Mac. Its PyTorch 2.8 dependency has no Intel Mac build.')
        return resource_root() / 'requirements-engine-macos-arm64.txt'
    return None


def engine_record(device):
    record = {'whisperx': ENGINE_VERSION}
    lock = engine_lock(device)
    if lock:
        record['requirements_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    return record


def setup_commands(device):
    lock = engine_lock(device)
    uv = tool_path("uv")
    if not uv:
        import uv as uv_package
        uv = uv_package.find_uv_bin()
    root = engine_dir(device)
    python_version, python_mirror = '3.11', []
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        mirror = resource_root() / 'python-install'
        try:
            manifest = json.loads((mirror / 'manifest.json').read_text())
            python_version = manifest['version']
            if not re.fullmatch(r'3\.11\.\d+', python_version):
                raise ValueError('Unsupported bundled Python version.')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('The bundled Python installer is unavailable. Reinstall the Mac application.') from exc
        python_mirror = ['--mirror', mirror.resolve().as_uri()]
    commands = [[str(uv), "python", "install", python_version, "--no-bin", "--no-registry", "--no-config", *python_mirror]]
    if not engine_python(device).exists() or engine_start_error(device):
        commands.append([str(uv), "venv", "--python", "3.11", "--managed-python", "--no-config", "--allow-existing", str(root)])
    if lock:
        if not lock.is_file():
            raise ValueError(f'The engine dependency lock is missing: {lock}. Reinstall the application.')
        backend = ['--torch-backend', 'cpu'] if device == 'cpu' and sys.platform != 'darwin' else []
        commands.append([str(uv), 'pip', 'sync', str(lock), '--python', str(engine_python(device)),
                         '--require-hashes', '--no-build', '--no-config', *backend])
        commands.append([str(engine_python(device)), '-m', 'transcript_desk.engine_check', str(lock)])
    else:
        commands.extend([
            [str(uv), "pip", "install", "--python", str(engine_python(device)), "--torch-backend", "cpu" if device == "cpu" else "cu128",
             "--no-config", "-r", str(resource_root() / "requirements-engine.txt")],
            [str(engine_python(device)), "-m", "transcript_desk.engine_check"],
        ])
    return commands


def extracted_windows_python():
    """Find the extracted interpreter without following minor-version links."""
    folder = runtime_root() / "python"
    candidates = []
    if not folder.is_dir():
        return None
    for directory in folder.iterdir():
        match = re.fullmatch(r"cpython-3\.11\.(\d+)-windows-x86_64-none", directory.name)
        if not match:
            continue
        attributes = directory.lstat()
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            continue
        python = directory / "python.exe"
        if python.is_file():
            candidates.append((int(match[1]), python))
    return max(candidates, default=(0, None))[1]


def python_link_recovery(output, root, remaining):
    """Recover only from link error 448; the returned commands first check that the extracted Python starts."""
    if sys.platform != "win32" or not (
        "failed to create python minor version link directory" in output.casefold()
        and "os error 448" in output.casefold()
    ):
        return None
    python = extracted_windows_python()
    if python is None:
        return None
    check = ("import sys, struct, ssl, venv, ctypes; "
             "assert sys.version_info[:2] == (3, 11), sys.version; "
             "assert struct.calcsize('P') == 8, 'Python must be 64-bit'; "
             "print('Extracted Python verified:', sys.executable)")
    commands = [[str(python), "-I", "-c", check],
                [str(python), "-I", "-m", "venv", "--without-pip", "--copies", str(root)]]
    commands.extend(command for command in remaining if command[1:2] != ["venv"])
    return python, commands


def child_environment(token=""):
    from PySide6.QtCore import QProcessEnvironment
    env = QProcessEnvironment.systemEnvironment()
    for name in ("PYTHONHOME", "VIRTUAL_ENV"):
        env.remove(name)
    env.insert("PYTHONNOUSERSITE", "1")
    # Engine imports must not add bytecode files to the signed application bundle.
    env.insert("PYTHONDONTWRITEBYTECODE", "1")
    env.insert("PYTHONPATH", str(resource_root()))
    # python -m would otherwise import modules from the window's working folder.
    env.insert("PYTHONSAFEPATH", "1")
    env.insert("PYTHONUNBUFFERED", "1")
    env.insert("PYTHONIOENCODING", "utf-8")
    env.insert("HF_HUB_DISABLE_TELEMETRY", "1")
    env.insert("PYANNOTE_METRICS_ENABLED", "0")
    if getattr(sys, "frozen", False) and sys.platform != 'win32':
        # Qt hooks and a frozen loader must not redirect an independent Python.
        for name in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
            env.remove(name)
        for name in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH'):
            original = env.value(name + '_ORIG')
            env.remove(name)
            if original:
                env.insert(name, original)
    bundled_tools = resource_root() / 'tools'
    if bundled_tools.is_dir():
        env.insert('PATH', str(bundled_tools) + os.pathsep + env.value('PATH'))
        if sys.platform == 'darwin':
            # TorchCodec opens FFmpeg dylibs by name in the separate engine process.
            env.insert('DYLD_FALLBACK_LIBRARY_PATH', str(bundled_tools / 'lib') + ':/usr/local/lib:/usr/lib')
            env.insert('TRANSCRIPT_DESK_FFMPEG_LIB', str(bundled_tools / 'lib'))
    if sys.platform == "win32":
        env.insert("UV_SYSTEM_CERTS", "true")
        if getattr(sys, "frozen", False):
            bundle = str(resource_root()).casefold().rstrip("\\/")
            paths = env.value("PATH").split(os.pathsep)
            env.insert("PATH", os.pathsep.join(p for p in paths if not (
                p.casefold().rstrip("\\/") == bundle or p.casefold().startswith(bundle + "\\")
                or p.casefold().startswith(bundle + "/"))))
    # Windows uses Credential Manager; a token travels only in this process environment.
    env.remove("HF_TOKEN")
    if token:
        env.insert("HF_TOKEN", token)
    local = runtime_root()
    for name, directory in (("UV_DATA_DIR", "uv"), ("UV_PYTHON_INSTALL_DIR", "python"),
                            ("UV_PYTHON_BIN_DIR", "python-bin"), ("UV_CACHE_DIR", "package-cache"),
                            ("HF_HOME", "models"), ("TORCH_HOME", "torch")):
        env.insert(name, str(local / directory))
    return env


@contextmanager
def default_dll_search():
    """External Python must not inherit the frozen GUI's DLL search directory."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    buffer = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    previous = buffer.value or None
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(previous)


def start_process(process, command):
    if sys.platform != 'win32':
        from PySide6.QtCore import QProcess
        parameters = QProcess.UnixProcessParameters()
        parameters.flags = QProcess.UnixProcessFlag.CreateNewSession
        process.setUnixProcessParameters(parameters)
    # QProcess.start creates the Windows process synchronously.
    with default_dll_search():
        process.start(command[0], command[1:])


def engine_start_error(device):
    """Return why the installed engine interpreter cannot start, or '' if it starts.

    ready.json and Scripts/python.exe survive the loss of the base Python that the
    environment's launcher points to; the worker then exits before its first event.
    """
    command = [str(engine_python(device)), "-I", "-c", "import sys"]
    try:
        with default_dll_search():
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if completed.returncode:
        return (completed.stderr or completed.stdout).strip()[-1500:] or f"Exit code {completed.returncode}."
    return ""


def worker_command(config_path, device, transcribe):
    if transcribe:
        return [str(engine_python(device)), "-m", "transcript_desk.worker", str(config_path)]
    if getattr(sys, "frozen", False):
        name = 'TranscriptWorker.exe' if sys.platform == 'win32' else 'TranscriptWorker'
        return [str(resource_root() / "worker" / name), str(config_path)]
    return [sys.executable, "-m", "transcript_desk.worker", str(config_path)]


def validate_queue(jobs):
    """Validate a detached queue without changing or quarantining its source file."""
    if not isinstance(jobs, list):
        raise ValueError("The queue file is not a list.")
    identifiers = set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get('config'), dict):
            raise ValueError('A queue entry has no valid configuration.')
        for key in ('id', 'source', 'status'):
            if not isinstance(job.get(key), str) or not job[key]:
                raise ValueError(f'A queue entry has no valid {key}.')
        if job['id'] in identifiers:
            raise ValueError('The queue contains duplicate recording identifiers.')
        identifiers.add(job['id'])
        config = job['config']
        if not isinstance(config.get('output'), str) or not config['output']:
            raise ValueError('A queue entry has no output directory.')
        if config.get('source_type') not in ('file', 'url', 'project'):
            raise ValueError('A queue entry has an invalid source type.')
        for key in ('transcribe', 'save_audio', 'save_video'):
            config.setdefault(key, False)
            if not isinstance(config[key], bool):
                raise ValueError(f'A queue entry has an invalid {key} option.')
        for key in ('title', 'message'):
            if key in job and not isinstance(job[key], str):
                raise ValueError(f'A queue entry has an invalid {key}.')
        if job["status"] == "running":
            job["status"] = "interrupted"
            job["message"] = "Interrupted by a previous application exit. Resume uses the last completed stage if available; Retry from start creates a separate attempt."
    return jobs


class QueueStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_root() / "queue.json"
        self.jobs = []
        self.load_error = ""
        self.read_only = False
        if self.path.exists():
            try:
                self.jobs = json.loads(self.path.read_text(encoding="utf-8"))
                validate_queue(self.jobs)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.load_error = f"The previous queue could not be read: {exc}"
                try:
                    self.path.rename(self.path.with_name(f"queue-unreadable-{uuid.uuid4().hex[:8]}.json"))
                except OSError as backup_error:
                    self.read_only = True
                    self.load_error += f' The original queue was preserved; queue saving is disabled: {backup_error}'
                self.jobs = []

    def save(self):
        if self.read_only:
            raise OSError('The unreadable queue could not be backed up. Restore write access and restart before changing the queue.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.{self.path.name}.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(self.jobs, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            replace_file(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def add(self, source, source_type, options):
        uid = uuid.uuid4().hex[:12]
        date = datetime.now().strftime("%Y%m%d-%H%M%S")
        config = {**options, "source": source, "source_type": source_type,
                  "output": str(Path(options["output"]) / f"{date}-{uid}")}
        job = {"id": uid, "source": source, "status": "queued", "message": "Waiting", "config": config}
        self.jobs.append(job)
        try:
            self.save()
        except OSError:
            self.jobs.remove(job)
            raise
        return job
