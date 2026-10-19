# Processing errors and retained transcripts

Each recording passes through these stages: loading the speech model, transcription, word alignment, loading the speaker model, speaker identification, speaker assignment and export. When one fails, the application records which stage failed and, separately, what kind of error it was.

## What is kept after a failure

Word alignment and the speaker stages run after transcription, so a failure in them does not lose the transcript. If alignment fails, the transcript keeps its segment timings without word timings. If a speaker stage fails, the transcript is kept with incomplete speaker identification. In both cases the recording is marked Complete with warnings and can be reviewed, and any exports requested before review are written from the kept transcript.

A failure while loading the speech model or transcribing ends the recording, and the error says whether an earlier transcript file exists. A failure during export also ends it, but the saved transcript stays available for review.

## Resuming

A recording marked Complete with warnings keeps its `.processing` folder with the last successful checkpoint. Resume retries the unfinished stages in the same folder and does not repeat a finished transcription: after a speaker failure it reuses the transcription and the alignment, and after an alignment failure it reuses the transcription and repeats alignment before the speaker stages. The input checksum, settings and engine versions must match the first attempt. A successful resume removes `.processing`.

Retry from start makes a new attempt in a separate folder and leaves the earlier attempt's files in place. Edits saved in `reviewed.json` are never overwritten; later machine timings and speaker assignments are not merged into them.

## Error categories

Categories come from the exception type or from the error the library reported. A model-access error requires an access refusal while a model was loading, and memory, processor, network, storage and dependency errors are recognised in the same way. Other errors are recorded as `unknown`, with the stage at which they happened.

A worker that crashes is recorded as `native_crash`, and one that exits without reporting completion as `unexpected_exit`. Both record the exit code and the last stage reported. When the worker sent no structured error, its last output is saved in the details.

## Reading and sharing the details

Select the recording and open Processing error details… to see the explanation, the suggested action and the original error text. Copy details adds the stage and category and removes known Hugging Face tokens. The original error text can contain recording paths or messages from dependencies, so read it before sharing it. The processing log shows only the summary.

Projects → Export diagnostic logs… writes a separate archive. For processing failures it holds only the stage, the category, whether a partial result exists and the exit code. It leaves out the error text, transcripts, project files and credentials.

## Record format

The queue stores issues under `processing_issues`, and saved results keep recoverable ones under `metadata.processing_issues`. Each record has the fields `stage`, `stage_label`, `category`, `summary`, `action`, `details` and `partial`. A fatal failure that leaves a saved transcript also writes `metadata.failure_issue` to `result.json`.

## Testing

`run_app.py --processing-fault-smoke-test --smoke-report <report.json>` simulates failures in recognition, alignment, speaker loading, identification and assignment, and checks the kept exports, Copy details and resuming from a checkpoint. It downloads no models and uses no real recordings. The Windows and Mac builds run it on the packaged application. Its diagnostics are written to `diagnostics/<report-stem>/` beside the report.
