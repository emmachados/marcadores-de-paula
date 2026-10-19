# Building

The application is a PySide6 interface (`transcript_desk/`, started by `run_app.py`) and a separate download worker (`worker_entry.py`). Speech processing does not run in the packaged application itself: Setup creates a private Python 3.11 environment, the engine, from the locked requirement files, and each recording is processed in a child process of that environment.

## Dependency locks

The engine's dependencies are pinned with SHA-256 hashes, 111 distributions per platform:

| File | Platform |
| --- | --- |
| `requirements-engine-windows-cpu.txt` | Windows x64, CPU |
| `requirements-engine-windows-cuda.txt` | Windows x64, NVIDIA (CUDA 12.8 wheels for PyTorch, torchaudio and torchvision) |
| `requirements-engine-macos-arm64.txt` | Apple silicon |

Setup installs with required hashes and no source builds, then compares every installed version with the lock before importing WhisperX. `ready.json` in the engine folder records the lock's SHA-256; Repair engine reapplies the current lock. The CUDA lock takes TorchCodec from PyPI, because no Windows CUDA build of it exists on the PyTorch index. To regenerate the Windows locks with uv 0.13.0:

```sh
uv pip compile requirements-engine.txt --python-version 3.11 --python-platform x86_64-pc-windows-msvc --torch-backend cpu --generate-hashes --no-build -o requirements-engine-windows-cpu.txt
uv pip compile requirements-engine-windows-cuda.in --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes --no-build -o requirements-engine-windows-cuda.txt
```

Recognition runs with CPU `int8` and batch size 1, or NVIDIA `float16` and batch size 4.

The Silero voice-activity model is loaded through Torch Hub with `source='local'` when its cache is complete, meaning nonempty `hubconf.py`, `src/silero_vad/utils_vad.py` and `src/silero_vad/data/silero_vad.jit` under `snakers4_silero-vad_main` or `snakers4_silero-vad_master`. This skips the GitHub request that the WhisperX 3.8.6 constructor makes before checking its cache, and keeps its onset of 0.500 and 30-second chunks. Without a complete cache, WhisperX downloads the model on first use.

## Windows

### FFmpeg

The installer bundles static `ffmpeg.exe` and `ffprobe.exe` cross-compiled from the official FFmpeg 7.1.3 source with mingw-w64. `packaging/windows-tools.json` pins the source archive and its SHA-256, and `packaging/build-ffmpeg-windows.sh` holds the recipe:

```
--arch=x86_64 --target-os=mingw32 --cross-prefix=x86_64-w64-mingw32- --pkg-config=false
--enable-static --disable-shared --disable-doc --disable-debug --disable-autodetect
--disable-ffplay --extra-ldflags=-static
```

No external libraries and no GPL or nonfree components are enabled, so the result is licensed under the LGPL 2.1 or later. The native decoders cover what the application asks of FFmpeg: extracting audio to FLAC or PCM, probing streams, and the remuxing that yt-dlp performs. The script writes the binaries, the LGPL text, the source archive, the recipe and a `build.json` record of the configure line, compiler and binary hashes to `build/windows-ffmpeg/`. `packaging/prepare-windows-tools.ps1` checks all of these against the manifest, refuses a build that reports `--enable-gpl` or `--enable-nonfree`, and copies the binaries to `tools/`. The installed application carries the source and recipe in `licenses/FFmpeg/`.

The build needs a Linux, WSL or Mac shell with mingw-w64 and nasm:

```sh
bash packaging/build-ffmpeg-windows.sh
```

### Installer

On Windows, with Python 3.11, Inno Setup 6, Deno and uv on `PATH`, and `build/windows-ffmpeg` copied into the checkout:

```powershell
./packaging/prepare-windows-tools.ps1
./packaging/build-windows.ps1
```

`build-windows.ps1` runs the test suite, packages the interface and the worker with PyInstaller, adds FFmpeg, Deno and uv, and collects the licence files of every bundled Python distribution, Python itself, Deno and Qt into `licenses/`. It then runs the packaged and installed checks described below and writes `release/MarcadoresDePaula-Setup-<version>-stock.exe` with its `.sha256`. The installer is per-user, needs no administrator rights and is unsigned.

The build can also bundle a recognition model pack, which the application then selects at startup:

```powershell
./packaging/build-windows.ps1 -ModelManifest path/to/model.json -ModelArchive path/to/model.zip
```

The manifest names the archive, the model directory inside it, its SHA-256 hashes, the display name and the language; `scripts/verify_release_model.py` checks the archive against it before extraction. The archive defaults to `release/<archive named in the manifest>`, and the manifest's `edition` replaces `stock` in the installer name.

A model pack is a folder holding a CTranslate2 conversion of large-v3 or large-v3-turbo (`model.bin`, `config.json`, `tokenizer.json`, `preprocessor_config.json`) and a `paula-model.json` file with `schema` 1, `base_model`, `name`, `language`, `training_corpus` and an `evaluation` record. `language` must be `es`. Add recordings → Add experimental model… imports such a folder into an installed application (`transcript_desk/model_pack.py`).

### Continuous integration

`.github/workflows/windows.yml` runs on version tags and on manual dispatch. An `ubuntu-24.04` job cross-compiles FFmpeg and stores it in the Actions cache, keyed on the hash of the recipe and the manifest; the Windows job restores it from there. The Windows Server 2022 job then builds the installer and checks it:

- in the packaged folder: worker and FFmpeg conversion, default-model selection, native audio playback, a fresh CPU-engine installation through Setup, and a second installation with error 448 injected after Python extraction;
- after running the installer: playback, saving Setup settings, review editing and reopening, the workspace, editor, speaker, processing-fault and preservation checks, and pause and resume across separate launches, both with the Pause button and by closing the window;
- the presence of the FFmpeg source and licence files in the installed folder.

The processing-fault check uses a synthetic engine, so neural recognition is not exercised on Windows. Each native check is limited to 240 seconds and each CPU-engine installation to 1,500 seconds; a timeout ends the process tree and fails the build. A separate job installs the locked CUDA engine and checks its imports; it has no GPU and runs no inference.

Artifacts are `MarcadoresDePaula-Windows-x64-stock` (installer, checksum and verification records), `MarcadoresDePaula-Windows-reports-stock` (records and logs, uploaded even when a check fails) and `MarcadoresDePaula-Windows-CUDA-verification`. With the `upload-draft` input set, the workflow attaches the installer and a verification archive to an existing draft release whose tag matches the version and whose target is the built commit. It never publishes a release.

## Mac

The Mac build targets Apple silicon on macOS 26 and later, using PySide6 6.12.0. There is no Intel build: WhisperX 3.8.6 requires PyTorch 2.8.0, which has no Intel Mac wheels.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt cmake==4.4.4 ninja==1.13.2
bash packaging/prepare-macos-tools.sh
.venv/bin/python packaging/prepare-macos-qt.py
.venv/bin/python packaging/build-macos.py
.venv/bin/python packaging/verify-macos.py
```

`prepare-macos-tools.sh` downloads and checks the archives pinned in `packaging/macos-tools.sha256`, then builds FFmpeg 7.1.3 as shared LGPL libraries without external codecs. It also bundles Deno 2.9.5, uv 0.13.0 and a standalone Python 3.11.17 archive, which Setup installs from a local mirror (`packaging/macos-python.json`).

`prepare-macos-qt.py` rebuilds Qt's Cocoa platform plugin from the official Qt 6.12.0 source with [upstream change 772484](https://codereview.qt-project.org/c/qt/qtbase/+/772484) applied (`packaging/qt-cocoa/`). The released plugin crashed during accessibility inspection of the transcript table; the change removes the deletion of accessibility interfaces during native element teardown and keeps accessibility enabled. `packaging/qt-cocoa/manifest.json` pins the source archives, the patch and the CMake and Ninja versions. The source, patch and recipe are copied into the application with the FFmpeg source and the other licence files.

`verify-macos.py` copies the application to a separate location and runs its interface, playback, review, workspace, editor, processing-fault and preservation checks with a system-only `PATH`, verifying the signature before and after. `--setup` adds a fresh CPU-engine installation. `build-macos.py` accepts `--model-manifest` and `--model-archive` to bundle a model pack, as on Windows, and `verify-macos.py --model-manifest` checks that it is selected.

The build has an ad-hoc signature and is not notarized; a copy downloaded from elsewhere is blocked by Gatekeeper until it is allowed in System Settings. `.github/workflows/macos.yml` runs on version tags and on manual dispatch. It runs the tests, builds the application on the `macos-26` runner, verifies it with `--setup`, and keeps the disk image and reports as the artifact `MarcadoresDePaula-macOS-arm64-stock`. With the `upload-draft` input set, it attaches the disk image, its checksum and a verification archive to the matching draft release, under the same conditions as the Windows workflow.

## Running checks from source

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python run_app.py --workspace-smoke-test --smoke-report build/verification/workspace.json
.venv/bin/python run_app.py --editor-smoke-test --smoke-report build/verification/editor.json
.venv/bin/python run_app.py --processing-fault-smoke-test --smoke-report build/verification/processing-fault.json
.venv/bin/python run_app.py --preservation-smoke-test --smoke-report build/verification/preservation.json
```

Each check exits with a non-zero status on failure and writes a JSON report with `ok: true` on success. `scripts/capture_readme.py` renders the screenshots in `docs/images/` from a synthetic interview and generated audio; it needs FFmpeg on `PATH`. `packaging/verify-macos-transcription.py` prepares transcription jobs for public test recordings with the installed Mac application code, and with `--run` transcribes them once Setup has installed an engine. The default recording is `build/verification/jfk.flac`, the sample from the [Whisper repository](https://github.com/openai/whisper/blob/main/tests/jfk.flac); `--fixture` takes another file.

## Data folders during development

`TRANSCRIPT_DESK_DATA` replaces both the state folder (the queue and each recording's settings) and the runtime folder (engine, Python, caches, setup logs). `TRANSCRIPT_DESK_RUNTIME` replaces only the runtime folder. Either must name a local writable folder, and an engine is not moved when the variable changes, so Setup has to install one in the new location. The folder names keep the application's earlier name, Transcript Desk, so that upgrades find existing queues.

## Version number

The version is set once, in `transcript_desk/__init__.py`. The package metadata, the installer name, the Mac bundle and the draft-release tag (`v<version>`) all read it from there. `CITATION.cff` and `CHANGELOG.md` are updated by hand for each release.
