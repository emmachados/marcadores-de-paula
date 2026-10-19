# Troubleshooting

## Engine installation on Windows

Setup shows the current step and its elapsed time. The progress bar stays indeterminate while a package downloads or unpacks, which on its own is not a failure. When a step ends, Setup either moves on or shows the failure; select Install engine again to retry. Copy installation log copies the whole saved log, including lines no longer visible on screen. Logs are kept in the runtime folder under `setup-logs`. The Hugging Face token is never passed to the package installer.

| Symptom | What to do |
| --- | --- |
| `Failed to create Python minor version link directory`, `os error 448` | Retry Setup. For this error Setup checks the extracted interpreter and continues without the link. If it still fails, copy the complete log. |
| `os error 206`, path too long | Copy the path from the log. A shorter runtime folder (see `TRANSCRIPT_DESK_RUNTIME` under [where files are stored](../README.md#where-files-are-stored)) shortens installation paths. Windows long-path support depends on system policy, which the application does not change. |
| `os error 112`, disk full | Free space on the runtime drive and on the drive holding the Windows temporary folder, then retry. Unpacking needs more space than the finished installation. |
| `os error 32`, file in use | Close other copies of the application and retry. Restart Windows if the file stays locked. |
| `os error 5`, access denied | Check write access to the named folder and the protection history in Windows Security. The installer does not need administrator rights. |
| Certificate or proxy error | Check the system date and time. Setup uses the Windows certificate store; on a managed network the administrator may have to correct trusted certificates or proxy settings. |
| Timeout, DNS or connection failure | Restore the connection and retry. Completed downloads stay in the package cache, and an engine is marked installed only after its import check passes. |
| Missing `VCRUNTIME` or `MSVCP` DLL, `0xc0000135` | Install or repair the [Microsoft Visual C++ x64 Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist). |
| CUDA, cuBLAS, cuDNN or GPU memory error | Select CPU in Setup, install that engine and retry the recording. |
| SmartScreen or application-control block | The installer is unsigned. Compare its SHA-256 with the checksum file on the release page; if an organisation's policy blocks it, only the device administrator can allow it. |

The final import check may print a TorchCodec warning about pyannote's built-in audio decoder. The application decodes audio with FFmpeg and passes the samples to the speaker model, so that decoder is never used and the warning can be ignored. Check Setup's final status, and keep the full log if processing later fails.

The error-448 workaround follows a matching [uv issue](https://github.com/astral-sh/uv/issues/19616). External Python processes are started with the packaged application's DLL search directory reset, as described in [PyInstaller's notes on launching external programs](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application).

## Starting the queue

If the engine is installed but its Python cannot start, Start queue stops and shows the Windows error. Open Setup and choose Repair engine; downloaded models are kept.

## Processing errors

Select the recording and open Processing error details…. The dialog names the failed stage and an error category, suggests an action and has Copy details; known Hugging Face tokens are removed from the copied text, but local paths are not. [Processing errors and retained transcripts](processing-errors.md) describes the categories and what is kept after each kind of failure.

- Speaker model refused: check the token and that the model's access conditions were accepted, using Check model access in Setup.
- NVIDIA processor or memory error: select CPU in Setup and install that engine.
- Complete with warnings: alignment or speaker processing failed after recognition. Resume retries the unfinished stages without repeating recognition.
- Failed or Interrupted: Resume works when a processing checkpoint exists. Retry from start creates a new output folder and leaves the earlier attempt untouched.

A URL can fail even when it is valid and public, because sites change what yt-dlp needs to extract media. Playlists and sign-in-only videos are not supported.

## Moved files and saving

If a recording's audio has moved, use Locate audio…. If autosave reports an error, restore write access or free disk space before continuing; saving is retried, but edits made since the last successful checkpoint are lost if the application exits.

## Diagnostics

Projects → Export diagnostic logs… writes a local archive with version information, exception frames and a native-fault log. It contains no transcript text, exception messages or credentials. Include it, together with the copied error details, when reporting a problem.
