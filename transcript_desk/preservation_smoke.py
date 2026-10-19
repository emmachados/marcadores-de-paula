"""Exercise earlier-version import and backup in the packaged application UI."""
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog


def run_preservation_check(application, window, report_path):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def check():
        from transcript_desk.preservation import prepare_upgrade, previous_work
        from transcript_desk.preservation_ui import PreviousWorkDialog, import_previous_work, backup_work
        from transcript_desk.review_storage import atomic_write, read_review
        from transcript_desk.annotations import fingerprint
        original_directory = QFileDialog.getExistingDirectory
        original_exec = PreviousWorkDialog.exec
        report = {'ok': False}
        temporary = None
        quit_on_close = application.quitOnLastWindowClosed()
        application.setQuitOnLastWindowClosed(False)
        try:
            temporary = tempfile.TemporaryDirectory(prefix='paula-earlier-work-')
            root = Path(temporary.name)
            old, project, exports = root / 'old-data', root / 'recordings/earlier-recording', root / 'exports'
            for folder in (old, project, exports):
                folder.mkdir(parents=True)
            review = {'metadata': {'title': 'Earlier recording'},
                      'speaker_names': {'SPEAKER_00': 'Earlier participant'},
                      'annotation_schema': 1,
                      'segments': [{'start': 0, 'end': 2, 'speaker': 'SPEAKER_00', 'text': 'Bueno, saved words.'}],
                      'annotations': [{'id': 'old-annotation', 'segment': 0, 'start_char': 0, 'end_char': 5,
                                       'quote': 'Bueno', 'text_sha256': fingerprint('Bueno, saved words.'),
                                       'label': 'Discourse marker', 'function': 'Unspecified', 'status': 'confirmed',
                                       'origin': 'manual', 'note': 'An earlier saved note.', 'start': 0, 'end': 2, 'timing': 'turn'}]}
            atomic_write(project / 'reviewed.json', review)
            original_review = (project / 'reviewed.json').read_bytes()
            (project / '.revisions').mkdir()
            atomic_write(project / '.revisions/earlier.json', review)
            job = {'id': 'legacy-recording', 'source': 'fixture.wav', 'status': 'complete',
                   'config': {'source': 'fixture.wav', 'source_type': 'file', 'output': str(project),
                              'transcribe': True, 'save_audio': True, 'save_video': False}}
            atomic_write(old / 'queue.json', [job])
            window.settings.setValue('theme', 'light')
            upgrade = prepare_upgrade(old, window.settings)
            assert upgrade and (upgrade / 'projects/1/reviewed.json').read_bytes() == original_review
            QFileDialog.getExistingDirectory = lambda *args, **kwargs: str(old)

            def preview_and_accept(dialog):
                def capture():
                    dialog.grab().save(str(report_path.with_suffix('.png')))
                    dialog.accept()
                QTimer.singleShot(150, capture)
                return original_exec(dialog)

            PreviousWorkDialog.exec = preview_and_accept
            import_previous_work(window)
            assert len(window.store.jobs) == 1
            assert window.open_project_folder(project)
            assert window.result['speaker_names']['SPEAKER_00'] == 'Earlier participant'
            assert window.result['annotations'][0]['note'] == 'An earlier saved note.'
            assert window.result['annotations'][0]['status'] == 'confirmed'
            assert window.result['annotation_codebook']['labels']
            window.transcript.item(0, 3).setText('Bueno, a new saved edit.')
            QFileDialog.getExistingDirectory = lambda *args, **kwargs: str(exports)
            backup_work(window)
            exported = next(exports.iterdir())
            recovered = previous_work(exported)[0][0]
            recovered_review = read_review(Path(recovered['config']['output']) / 'reviewed.json')
            assert recovered_review['segments'][0]['text'] == 'Bueno, a new saved edit.'
            assert (Path(recovered['config']['output']) / '.revisions/earlier.json').is_file()
            import_previous_work_source = previous_work(old)[0]
            from transcript_desk.preservation_ui import apply_previous_work
            assert apply_previous_work(window, import_previous_work_source) == (0, 1)
            window.grab().save(str(report_path.with_name(report_path.stem + '-workspace.png')))
            report.update(upgrade_backup=True, imported_legacy_review=True, preserved_annotation=True,
                          retained_speaker_name=True, retained_codebook=True, retained_named_revision=True,
                          exported_latest_edit=True, duplicate_skipped=True)
            window.review_writer.flush()
            window.complete_review_saves()
        except Exception as exc:
            report.update(ok=False, error=repr(exc))
        finally:
            # Release native project/audio handles before deleting the fixture on Windows.
            # Retain the assertion failure if closing or fixture cleanup also fails.
            cleanup_errors = []
            try:
                if not window.close():
                    raise RuntimeError('The preservation smoke window could not be closed.')
            except Exception as exc:
                cleanup_errors.append('Window close: ' + repr(exc))
            try:
                if window.project_lock:
                    window.project_lock.release()
            except Exception as exc:
                cleanup_errors.append('Project lock release: ' + repr(exc))
            try:
                if temporary is not None:
                    temporary.cleanup()
            except Exception as exc:
                cleanup_errors.append('Fixture cleanup: ' + repr(exc))
            if cleanup_errors:
                report['cleanup_errors'] = cleanup_errors
                report.setdefault('error', cleanup_errors[0])
            report['ok'] = 'error' not in report
            application.setQuitOnLastWindowClosed(quit_on_close)
            QFileDialog.getExistingDirectory = original_directory
            PreviousWorkDialog.exec = original_exec
            report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
            application.exit(0 if report['ok'] else 1)

    QTimer.singleShot(100, check)
