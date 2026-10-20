# Changelog

From 0.9.0, the Windows installer and the Mac disk image are attached to each [GitHub release](https://github.com/emmachados/marcadores-de-paula/releases), with SHA-256 checksums and the verification records from the builds that produced them. On Windows, install a newer version over an older one; on a Mac, replace the application in Applications. The queue, recording folders and saved reviews are kept.

## Unreleased

- TikTok links now download the audio recorded in the clip. Earlier versions took TikTok's separate audio track, which for a post with a song attached is the song, so the transcript did not match the video.

## 0.9.0 (19 October 2026)

First release from this repository, and the first with a Mac download, for Apple silicon on macOS 26 or later. Earlier installers were distributed privately.

- When the engine's Python can no longer start, for example because the Python installation it was created from has been removed, Start queue now stops before processing and shows the Windows error. Earlier versions reported the engine as ready, and every recording then failed within a second at "Processing worker startup". Repair engine in Setup rebuilds the environment in this case and keeps downloaded models.
- On a Mac, Setup offers only the CPU engine. It previously also listed the NVIDIA engine, which macOS cannot run.
- A worker that exits without a structured error now has its last output saved in the error details.
- The worker runs from the application's own job folder and ignores Python modules in the folder the application was started from.
- The Windows installer bundles an LGPL build of FFmpeg 7.1.3 compiled from the official source, and ships that source and the build recipe with it. Earlier Windows installers bundled a third-party GPL build.
- The Windows installer includes the licence texts of every bundled component.
- The source code is licensed under GPL-3.0-or-later.

## 0.8.1 (16 October 2026)

- Typing a name in a speaker cell reassigns only that turn. A name that matches an existing speaker joins that speaker; a new name creates one. Typing Unassigned clears the assignment. Review → Manage speakers… still renames a speaker throughout the recording, and lists identities that share a name so that duplicates created by earlier versions can be merged.
- Speaker assignment, undo and inline speaker edits no longer block the window on long recordings. On a synthetic one-hour recording with 830 turns, assigning one turn took 20 seconds before and about 0.2 seconds after, on Apple silicon.
- Manual speaker assignments are also written to the word records kept from recognition, which appear in JSON exports.
- New installations save recordings in the Documents folder that Windows reports, which may be inside OneDrive. An existing `Documents\Marcadores de Paula` folder remains the default. Saving retries briefly when a synchronisation client or virus scanner holds a project file open.

## 0.8.0 (14 October 2026)

- Speaker text tab: turns grouped by speaker, with optional timestamps and paragraph breaks, copy, export of the view, and a jump back to the timed turn.
- Find and replace with context, filters by speaker and time range, and a previewed replacement that undoes as one step.
- Manage speakers dialog with names, colours, notes, bulk assignment and identity merging. Bookmarks, a comparison of the machine and current wording, named snapshots and a revision browser.
- Project codebook for annotations: 27 label and 21 function presets that can be renamed, archived, grouped and given shortcuts. Several labels per passage, multi-turn records, timed events, certainty, author, dates, links between records, filters and bulk review.
- Export presets with speaker and annotation selection, timestamp-free text, per-speaker folders, annotation tiers in EAF and TextGrid, annotated DOCX, concordances and counting tables.
- Waveform view with interval selection, interval playback, looping and listening context.
- Portable `.paula.zip` projects with optional media.
- Processing errors name the failed stage and an error category, with a Copy details button. Alignment and speaker failures keep the transcript and finish as Complete with warnings; Resume retries only the unfinished stages.
- Autosave runs in one background worker, and an older save can no longer mark newer edits as saved. A second copy of the application using the same data folder now exits before reading the queue.
- On upgrade, a safety copy of the queue, transcripts, reviews and revision history is written before the queue opens. Projects → Back up work… and Projects → Import previous work… were added.
- The cached voice-activity model loads from disk without a network request.
- First Apple silicon build, for macOS 26.

## 0.7.0 (14 October 2026)

- Pause stops the current recording and leaves the rest of the queue pending. Resume continues it, also after the application has been closed and reopened. Closing during processing pauses the current recording.
- Completed stages (media preparation, recognition, alignment, speaker assignment) are saved in the recording folder and reused on Resume. An interrupted stage starts again from its beginning.
- Elapsed recording and stage times in the interface, written to the log and to `source.json`.
- Check speakers steps through turns with missing assignments or word timings that match more than one speaker.
- Setting Speakers to 1 assigns all speech to one speaker without running the speaker model or needing an access token.
- Assigning speakers to words uses a restricted interval search. On a synthetic three-hour timeline with 3,600 speaker intervals and 36,000 words, the median time fell from 0.212 to 0.029 seconds with identical labels.

## 0.6.1 (13 October 2026)

- Fixes a crash when choosing Save and close in Setup. The button's name shadowed Qt's `done()` method.

## 0.6.0 (13 October 2026)

- Edit turn dialog: assign a speaker to one turn, create a speaker, correct start and end times, split a turn at a text position and audio time, and merge with the next turn.
- Undo and Redo for up to 20 review changes per recording.
- Open project reopens a saved recording folder from its newest valid review without transcribing again.
- Windows CPU and CUDA engines install from separate lock files with 111 exact package versions and required hashes.

## 0.5.2 (13 October 2026)

- When Windows refuses the Python version link during engine installation (error 448), Setup checks the extracted interpreter and continues without the link.

## 0.5.1 (13 October 2026)

- Fixes the Setup window that could not advance or report a failed step in 0.4.0 and 0.5.0.
- New engines and caches go to local application data instead of the roaming profile.
- Setup shows the step and elapsed time, saves the full installation log and can copy it.
- Package downloads use the Windows certificate store.

## 0.5.0 (12 October 2026)

- A model pack bundled with the installer is selected at startup.

## 0.4.0 (12 October 2026)

- First published Windows installer: download queue, WhisperX transcription with large-v3-turbo or large-v3, word alignment, pyannote speaker diarization, audio-linked transcript review with autosave, discourse annotations and eight export formats. Model packs in CTranslate2 format can be imported through Add recordings → Add experimental model. Engine installation in this version fails on some machines; use 0.5.2 or later.
