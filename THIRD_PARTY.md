# Third-party components

Marcadores de Paula is licensed under the GNU General Public License, version 3 or later (see `LICENSE`). The components below keep their own licences. Packaged builds copy the licence files of every bundled Python distribution, of Python itself, of Deno and of Qt into the application's `licenses` folder.

## Bundled with the application

| Component | Licence | Notes |
| --- | --- | --- |
| Qt and PySide6 6.12.0 | LGPL-3.0 | Loaded as shared libraries. The LGPL text is in `licenses/LGPL-3.0.txt`. The Mac build replaces the Cocoa platform plugin with one rebuilt from Qt source; that source, the patch and the recipe are included in the Mac application. |
| FFmpeg 7.1.3 | LGPL-2.1-or-later | Built from the official source without external libraries or GPL components. The Windows installer includes the source archive and build recipe in `licenses/FFmpeg/`; the Mac application includes the same in its `licenses` folder. |
| yt-dlp | Unlicense | Some of its dependencies have their own licences, copied with the package. |
| Deno 2.9.5 | MIT | Used by yt-dlp for YouTube extraction. |
| uv 0.13.0 | MIT or Apache-2.0 | Installs the processing engine. |
| Python 3.11 | PSF License | Interpreter of the packaged application; the Mac build also bundles the interpreter used for the engine. |
| keyring | MIT | |
| python-docx | MIT | |

## Installed by Setup

The processing engine is downloaded and installed on the user's computer from the locked requirement files. On Windows, Setup first downloads a standalone CPython 3.11 build through uv (python-build-standalone, PSF License); the Mac application bundles that interpreter. The main components are WhisperX (BSD-2-Clause), faster-whisper (MIT), CTranslate2 (MIT), PyTorch (BSD-3-Clause) and pyannote.audio (MIT). Each package's licence is installed with it.

## Models

Whisper large-v3 and large-v3-turbo are released by OpenAI under the MIT licence (`licenses/whisper-MIT.txt`) and are downloaded on first use. The pyannote speaker-diarization-community-1 model is licensed CC-BY-4.0 and is only downloaded after the user accepts its access conditions on Hugging Face.

## Sources

[Qt](https://www.qt.io), [PySide6](https://doc.qt.io/qtforpython-6/), [FFmpeg](https://ffmpeg.org), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [Deno](https://github.com/denoland/deno), [uv](https://github.com/astral-sh/uv), [WhisperX](https://github.com/m-bain/whisperX), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2), [pyannote.audio](https://github.com/pyannote/pyannote-audio), [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), [Whisper](https://github.com/openai/whisper).
