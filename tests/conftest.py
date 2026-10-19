import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_DESK_RUNTIME", raising=False)
    monkeypatch.setenv("TRANSCRIPT_DESK_DATA", str(tmp_path / "app-data"))


@pytest.fixture
def result():
    return {
        "metadata": {"title": "Annotation fixture", "source": "fixture.wav", "duration": 5,
                     "created_at": "2026-10-12T10:00:00+00:00"},
        "speaker_names": {"SPEAKER_00": "Interviewer", "SPEAKER_01": "Participant"},
        "segments": [
            {"start": .5, "end": 1.8, "speaker": "SPEAKER_00", "text": 'The word is "café".'},
            {"start": 2, "end": 3.4, "speaker": "SPEAKER_01", "text": "Yes & no <sometimes>."},
            {"start": 4, "end": 4.6, "text": "Uncertain speaker."},
        ],
    }
