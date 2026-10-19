from __future__ import annotations

import sys


def main():
    if '--pause-smoke-test' in sys.argv:
        import os
        from pathlib import Path
        state_path = Path(sys.argv[sys.argv.index('--smoke-state') + 1]).resolve()
        state_path.mkdir(parents=True, exist_ok=True)
        os.environ['TRANSCRIPT_DESK_DATA'] = str(state_path)
    if any(flag in sys.argv for flag in ('--review-smoke-test', '--setup-save-smoke-test', '--workspace-smoke-test', '--editor-smoke-test', '--preservation-smoke-test', '--processing-fault-smoke-test', '--speaker-smoke-test')):
        import tempfile
        import os
        state = tempfile.TemporaryDirectory(prefix='paula-review-state-')
        os.environ['TRANSCRIPT_DESK_DATA'] = state.name
    from PySide6.QtWidgets import QApplication
    from transcript_desk.theme import stylesheet
    from transcript_desk.window import MainWindow
    application = QApplication(sys.argv)
    application.setOrganizationName("TranscriptDesk")
    application.setApplicationName("Transcript Desk")  # Stable storage location for existing installations.
    application.setApplicationDisplayName("Marcadores de Paula")
    from transcript_desk.instance_lock import ApplicationLock
    from transcript_desk.runtime import data_root
    from PySide6.QtWidgets import QMessageBox
    instance_lock = ApplicationLock(data_root())
    if not instance_lock.acquire():
        QMessageBox.information(None, 'Application data is in use or unavailable',
                                instance_lock.message + '\n\nApplication data: ' + str(instance_lock.folder))
        return 1
    application.aboutToQuit.connect(instance_lock.release)
    application.instance_lock = instance_lock
    from transcript_desk.diagnostics import install_diagnostics
    if '--smoke-report' in sys.argv:
        from pathlib import Path
        smoke_report = Path(sys.argv[sys.argv.index('--smoke-report') + 1]).resolve()
        diagnostics = install_diagnostics(folder=smoke_report.parent / 'diagnostics' / smoke_report.stem)
    else:
        diagnostics = install_diagnostics()
    if any(flag in sys.argv for flag in ('--setup-save-smoke-test', '--pause-smoke-test', '--workspace-smoke-test', '--editor-smoke-test', '--preservation-smoke-test', '--processing-fault-smoke-test', '--speaker-smoke-test')):
        from PySide6.QtCore import QSettings
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                          str(state_path) if '--pause-smoke-test' in sys.argv else state.name)
    application.setStyle("Fusion")
    application.setStyleSheet(stylesheet())
    from PySide6.QtCore import QSettings
    from transcript_desk.preservation import startup_preservation
    from transcript_desk.runtime import data_root
    preservation_backup, preservation_error = startup_preservation(data_root(), QSettings())
    window = MainWindow()
    if preservation_backup:
        window.message.setText('Previous work retained. Upgrade safety copy: ' + str(preservation_backup))
    if preservation_error:
        from PySide6.QtWidgets import QLabel
        window.preservation_warning = QLabel('Upgrade safety copy failed: ' + preservation_error +
                                            '. Existing work remains accessible. Use Projects → Back up work to retry.')
        window.preservation_warning.setWordWrap(True)
        window.statusBar().addPermanentWidget(window.preservation_warning, 1)
    if diagnostics is not None:
        from PySide6.QtCore import QTimer
        diagnostics.notify = lambda: QTimer.singleShot(0, lambda: window.show_error(
            'An application error was recorded. Save your review before continuing. '
            'Diagnostics: ' + str(diagnostics.folder)))
    window.show()
    if '--preservation-smoke-test' in sys.argv:
        from transcript_desk.preservation_smoke import run_preservation_check
        run_preservation_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if '--processing-fault-smoke-test' in sys.argv:
        from transcript_desk.processing_fault_smoke import run_processing_fault_check
        run_processing_fault_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if '--speaker-smoke-test' in sys.argv:
        from transcript_desk.speaker_smoke import run_speaker_check
        run_speaker_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if '--editor-smoke-test' in sys.argv:
        from transcript_desk.editor_smoke import run_editor_check
        run_editor_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if '--workspace-smoke-test' in sys.argv:
        from transcript_desk.workspace_smoke import run_workspace_check
        run_workspace_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if '--pause-smoke-test' in sys.argv:
        from transcript_desk.pause_smoke import run_pause_check
        run_pause_check(application, window,
                        sys.argv[sys.argv.index('--smoke-report') + 1],
                        sys.argv[sys.argv.index('--smoke-phase') + 1],
                        sys.argv[sys.argv.index('--pause-mode') + 1],
                        sys.argv[sys.argv.index('--smoke-python') + 1])
        return application.exec()
    if '--setup-save-smoke-test' in sys.argv:
        from transcript_desk.setup_save_smoke import run_setup_save_check
        run_setup_save_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if '--review-smoke-test' in sys.argv:
        from transcript_desk.review_smoke import run_review_check
        run_review_check(application, window, sys.argv[sys.argv.index('--smoke-report') + 1])
        return application.exec()
    if "--setup-smoke-test" in sys.argv:
        from transcript_desk.setup_smoke import run_setup_check
        run_setup_check(application, window, sys.argv[sys.argv.index("--smoke-report") + 1])
        return application.exec()
    if "--expect-stock" in sys.argv:
        options = window.options()
        if options["model"] != "large-v3-turbo":
            raise RuntimeError("The stock edition did not select large-v3-turbo.")
        if "--smoke-report" in sys.argv:
            import json
            from pathlib import Path
            Path(sys.argv[sys.argv.index("--smoke-report") + 1]).write_text(
                json.dumps({"model": options["model"], "language": options["language"]}))
    if "--expect-model-pack" in sys.argv:
        import json
        from pathlib import Path
        from transcript_desk.model_pack import inspect_pack
        expected = json.loads(Path(sys.argv[sys.argv.index("--expect-model-pack") + 1]).read_text())
        options = window.options()
        if options["model"] != "custom" or options["language"] != expected["language"]:
            raise RuntimeError("The packaged application did not select its bundled model pack.")
        _, metadata = inspect_pack(options["custom_model_path"])
        if metadata["name"] != expected["name"] or metadata["model_sha256"] != expected["model_sha256"]:
            raise RuntimeError("The packaged application selected the wrong model pack.")
        if "--smoke-report" in sys.argv:
            report = Path(sys.argv[sys.argv.index("--smoke-report") + 1])
            report.write_text(json.dumps({"model": metadata["name"], "language": options["language"],
                                          "model_sha256": metadata["model_sha256"]}, indent=2))
    if "--smoke-test" in sys.argv:
        from PySide6.QtCore import QTimer
        import tempfile
        import wave
        from pathlib import Path
        # Load the frozen multimedia backend as well as Qt.
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "playback.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 16000 * 3)
        window.player.audio.setMuted(True)
        window.player.load(path, "Playback verification")
        window.player.player.positionChanged.connect(lambda ms: application.exit(0) if ms >= 250 else None)
        window.player.player.play()
        QTimer.singleShot(15000, lambda: application.exit(1))
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
