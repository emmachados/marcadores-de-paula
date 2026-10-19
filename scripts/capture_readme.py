"""Capture the README screenshots from the running interface with a synthetic interview."""
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PySide6.QtCore import QItemSelectionModel, QLocale, QSettings
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication
import transcript_desk.window as module

RATE = 16000
TURNS = [
    ("SPEAKER_00", 0.0, 3.2, "¿Y cómo, cómo decidiste empezar?"),
    ("SPEAKER_01", 3.6, 7.8, "Pues, pues no sé, o sea, al principio me costó."),
    ("SPEAKER_00", 8.0, 8.6, "Ya."),
    ("SPEAKER_01", 8.9, 15.4, "Bueno, es que yo venía de otra cosa, ¿sabes?, de trabajar en una tienda, y claro, no tenía ni idea."),
    ("SPEAKER_00", 15.8, 17.2, "¿Y quién te animó?"),
    ("SPEAKER_01", 17.6, 23.0, "Mi hermana, mi hermana sobre todo. Ella me dijo: «mira, apúntate y ya está»."),
    ("SPEAKER_00", 23.3, 24.9, "Hombre, pues menos mal."),
    ("SPEAKER_01", 25.3, 32.0, "Sí, sí, la verdad es que sí. Y luego, eh, cuando empecé las prácticas, entonces ya lo vi claro."),
    ("SPEAKER_00", 32.4, 34.5, "O sea, que fue en las prácticas."),
    ("SPEAKER_01", 34.9, 37.0, "Eso es. Bueno, más o menos."),
]
PITCH = {"SPEAKER_00": 205.0, "SPEAKER_01": 175.0}


def segments():
    result = []
    for speaker, start, end, text in TURNS:
        tokens = text.split()
        weights = [max(1, sum(character in "aeiouáéíóú" for character in token.lower())) for token in tokens]
        step, cursor, words = (end - start) / sum(weights), start, []
        for token, weight in zip(tokens, weights):
            words.append({"word": token, "start": round(cursor, 3), "end": round(cursor + weight * step, 3),
                          "speaker": speaker, "score": 0.9})
            cursor += weight * step
        result.append({"start": start, "end": end, "speaker": speaker, "text": text, "words": words})
    return result


def write_audio(path, turns):
    """Write a speech-like signal: one voiced burst per syllable, at each speaker's pitch."""
    generator = random.Random(4)
    total = int((turns[-1]["end"] + 0.6) * RATE)
    signal = np.random.default_rng(4).normal(0, 0.004, total)
    for turn in turns:
        for word in turn["words"]:
            syllables = max(1, sum(character in "aeiouáéíóú" for character in word["word"].lower()))
            length = (word["end"] - word["start"]) / syllables
            for index in range(syllables):
                begin = int((word["start"] + index * length) * RATE)
                count = int(length * RATE * 0.85)
                t = np.arange(count) / RATE
                pitch = PITCH[turn["speaker"]] * (1 + 0.08 * math.sin(word["start"] + index))
                carrier = sum(np.sin(2 * math.pi * pitch * harmonic * t) / harmonic for harmonic in range(1, 6))
                burst = carrier * np.hanning(count) * generator.uniform(0.18, 0.42)
                signal[begin:begin + count] += burst[:max(0, total - begin)]
    samples = (np.clip(signal, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples.tobytes())


def wait(app, condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def main():
    with tempfile.TemporaryDirectory(prefix="paula-readme-") as temporary:
        work = Path(temporary)
        os.environ["TRANSCRIPT_DESK_DATA"] = str(work / "state")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(work / "settings"))
        module.resource_root = lambda: work
        QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedKingdom))
        app = QApplication([])
        app.setOrganizationName("ReadmePreview")
        app.setApplicationName("Marcadores de Paula")
        app.setStyle("Fusion")
        window = module.MainWindow()
        window.resize(1280, 1040)
        window.show()
        assert window.options()["model"] == "large-v3-turbo"
        window.output.setText(str(work / "recordings"))
        window.add_sources(["https://example.org/interview", "https://example.org/discussion"], "url")
        job = window.store.jobs[0]
        job.update(status="complete", title="Synthetic example · interview")
        folder = Path(job["config"]["output"])
        folder.mkdir(parents=True)
        turns = segments()
        write_audio(folder / "interview.wav", turns)
        result = {"metadata": {"title": job["title"], "media_path": "interview.wav",
                               "provenance": "Synthetic text and audio for interface screenshots"},
                  "speaker_names": {"SPEAKER_00": "Interviewer", "SPEAKER_01": "Participant"},
                  "segments": turns}
        (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        if window.theme == "dark":
            window.toggle_theme()
        window.refresh_queue()
        window.queue.selectRow(0)
        player = window.player.player
        wait(app, lambda: player.mediaStatus() == QMediaPlayer.LoadedMedia)
        window.engine_label.setText("CPU engine ready")
        window.review_hint.setText("Synthetic interview and audio, generated for this screenshot.")
        audio = window.workspace.audio
        audio.expand.setChecked(True)
        audio.load_waveform()
        wait(app, lambda: audio.process is None)
        turn = turns[7]
        audio.set_interval(turn["start"], turn["end"])
        position = turn["words"][9]["start"] + 0.05
        player.setPosition(round(position * 1000))
        wait(app, lambda: abs(player.position() - position * 1000) < 50, 3)
        audio.wave.position = position
        window.highlight_position(round(position * 1000))
        window.transcript.selectRow(7)
        destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/images"
        destination.mkdir(exist_ok=True)
        app.processEvents()
        window.grab().save(str(destination / "transcript-light.png"))

        window.toggle_theme()
        audio.expand.setChecked(False)
        window.tabs.setCurrentWidget(window.annotations)
        panel = window.annotations
        panel.find_button.click()
        candidates = panel.message.text()
        table = panel.table
        for row in (0, 2):
            table.selectionModel().select(table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        panel.batch_status("confirmed")
        table.clearSelection()
        table.selectRow(0)
        panel.message.setText(candidates)
        table.resizeColumnToContents(1)
        app.processEvents()
        window.grab().save(str(destination / "annotations.png"))

        window.toggle_theme()
        dialog = window.import_dialog
        window.output.setText(r"C:\Users\researcher\Documents\Marcadores de Paula")
        window.import_feedback.hide()
        dialog.resize(560, 1000)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(destination / "add-recordings.png"))
        dialog.close()
        window.close()
        print(destination)


if __name__ == "__main__":
    main()
