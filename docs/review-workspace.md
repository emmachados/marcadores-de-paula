# Review workspace

All views, edits and exports use the timed transcript. Each turn has a stable `turn_id`, and codebook labels and functions have identifiers that do not change when they are renamed. Projects saved before 0.8.0 receive these identifiers when opened and keep them from the next save; the original `result.json` is never modified.

## Speaker text

The Speaker text tab shows one speaker or all of them. With Group by speaker on, Show timestamps and Keep turn paragraphs can be switched independently; with both off, each speaker's turns run as continuous text in chronological order. Copy text copies the selection, or the whole view when nothing is selected. Go to source turn opens the timed turn for editing and playback. Export this view writes TXT and DOCX with the same layout.

## Find and replace

Review → Find and replace… (Ctrl+F, or Cmd+F on a Mac) searches the literal wording. Results can be limited by case, whole words, speaker and time range, and each result shows its context. Replace selected and Replace all each make one change that a single Undo reverses. If the transcript has changed since the preview was made, the replacement is refused. Annotations outside the replaced text move with it; annotations that overlap it are marked for review.

## Speakers, bookmarks and revisions

Review → Manage speakers… edits names, colours and notes. To assign several turns at once, select their cells in the transcript before opening it. Merging two identities shows how many turns will change and is undone as one step; the turns keep their own intervals. The Review menu also holds bookmarks, a comparison of the machine wording with the current wording, and the revision controls. Bookmarks are saved in the project and point to turn identifiers. The reading and audio positions are remembered on the local computer for each project folder.

## Annotation codebooks

Codebook… opens the project's labels and functions. A new project starts with 27 labels and 21 functions. Categories can be added, renamed, archived, grouped, marked as favourites and given shortcuts, and the list of literal forms that Find candidates searches for can be edited. Importing a codebook JSON file merges categories by identity and keeps those already used by records. Annotation exports include the codebook.

Besides discourse markers, filled pauses, restarts, overlaps, laughter and lengthening, the starting labels include self-correction, correction by another speaker, self-repetition, repetition of another speaker, interruption, breath, cough or throat clearing, background sound, unintelligible speech, reported speech, language change, emphasis, intonation, quiet and loud speech, responses and topic boundaries. Find candidates matches literal forms and adjacent repeated words and assigns no function.

To annotate a passage, select its wording and use Label selection, or Several labels… to give it more than one category. For a record that spans turns, choose the last turn under Through turn and use Label turn range. Timed event… marks a stretch of audio with no transcript text. A multi-turn record keeps each turn's span separately; when turns are split or merged, the record follows them and is marked for review.

Each record holds a review status, a separate certainty rating, the author, creation and modification dates, notes and links to related records. Records made by versions without dates keep those fields empty. With several rows selected, records can be confirmed, rejected, duplicated, deleted, relabelled or linked together. The filters combine text, speaker, label, function, status and time interval. Next unresolved steps through suggested records and those marked for review, and Listen plays the record's interval.

## Audio review

Waveform and interval review opens the audio tools below the player. Load waveform decodes the recording with FFmpeg in a separate process and keeps 100 amplitude peaks per second, one for every 80 samples at 8 kHz. Drag across the waveform to select an interval, and correct it in the Start and End fields. The zoom menu and the scroll bar show shorter stretches of long recordings. The display is an editing aid; use Praat for acoustic measurement.

Play interval stops at the end of the interval, or repeats it when Loop is on. Context adds the chosen number of seconds before and after. Previous turn and Next turn move to the neighbouring turn and play it, and Replay sets how far the skip-back button goes.

## Export

Export…, also Review → Export options… (Ctrl+Shift+E), previews the text and starts from the recording's preferred formats. Speakers, annotation categories and statuses can be selected, and the text can be written as continuous speaker text, with or without timestamps and paragraph breaks, or as one folder per speaker. Timed formats always use the source intervals. Presets save these choices; a speaker preset from another project asks for its speakers to be matched to this one.

EAF and TextGrid exports can include annotation tiers. Overlapping annotations go to separate tiers, a multi-turn annotation uses each speaker's own passage and interval, and annotations marked for review are left out of the timed tiers. Overlapping turns of one speaker still need EAF or JSON, since a TextGrid tier cannot hold them. The annotated DOCX highlights the selected passages in transcript order and lists the annotation records at the end.

The analysis tables are `concordance.csv`, with one row per source span, `annotation-counts.csv`, `speaker-summary.csv` and `counting-rules.json`. Counts keep review statuses apart and count each annotation once for each speaker it covers, so a record spanning two speakers counts for both; several labels on one passage remain separate records. Words are counted as Unicode word sequences, allowing internal apostrophes. Durations add up the source turns, so overlapping speech can be counted twice.

## Portable projects

Review → Create portable project… writes a `.paula.zip` file with the current review, the original transcript, the codebook, named and automatic revisions and, optionally, the media. Projects → Import portable project… unpacks it into a new folder and refuses entries with unsafe paths. Media paths are stored relative to the project, including those in snapshots. Recognition caches and processing checkpoints are not included.

## Saving and recovery

The review is saved two seconds after each change, including text still open in a cell. Save review, switching projects and closing the window wait for pending saves, and the project stays open if saving fails.

Each save goes to a new temporary file, which is flushed to disk and then replaces the previous file in one step. `autosave.previous.json` keeps the previous valid save. A project lock allows one writer at a time: another copy of the application can read and export the project, and can edit it after reopening it once the first copy has closed.

Automatic history in `.revisions` stores each changed manual save and at most one autosave per minute, up to 30 automatic revisions. Named snapshots are kept until they are deleted from the project folder. Restoring a snapshot first saves a recovery snapshot of the current review. Undo and Redo cover the last 20 changes in the current session; after a restart, earlier states are available through Restore snapshot….

## Diagnostics

The application keeps short records of versions and exception frames, and a separate log of native faults. These records exclude exception messages, transcript text and credentials. Projects → Export diagnostic logs… packs them into a local archive. Processing failures have their own records, described in [processing errors and retained transcripts](processing-errors.md).
