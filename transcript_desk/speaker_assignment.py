"""Assign by temporal intersection and retain competing speaker labels for review."""
from bisect import bisect_left, bisect_right


def needs_speaker_model(config):
    return config.get("transcribe", True) and config.get("diarize", False) and config.get("speakers") != 1


def assign_single_speaker(result):
    """Assign all speech to one speaker when the user sets the count to 1."""
    for segment in result.get("segments", []):
        segment["speaker"] = "SPEAKER_00"
        for word in segment.get("words", []):
            word["speaker"] = "SPEAKER_00"
    result["metadata"]["diarization"] = "single_speaker_declared"
    result["metadata"]["speaker_assignment_method"] = "user_supplied_single_speaker"
    result["metadata"]["speaker_intervals"] = []
    return result


class SpeakerIntervals:
    def __init__(self, intervals):
        self.intervals = sorted(intervals, key=lambda item: item[0])
        self.starts = [item[0] for item in self.intervals]
        self.latest_ends = []
        latest = float("-inf")
        for _, end, _ in self.intervals:
            latest = max(latest, end)
            self.latest_ends.append(latest)

    def candidates(self, start, end):
        scores = {}
        # Prefix maxima retain long intervals that overlap later, shorter ones.
        left = bisect_right(self.latest_ends, start)
        right = bisect_left(self.starts, end)
        for index in range(left, right):
            first, last, speaker = self.intervals[index]
            overlap = min(last, end) - max(first, start)
            if overlap > 0:
                scores[speaker] = scores.get(speaker, 0.0) + overlap
        return scores


def assign_speakers(intervals, result):
    """Match WhisperX's greatest-intersection rule without filling temporal gaps.

    Candidate durations are overlap times in seconds.
    Transcript wording, word order, and overlapping input intervals are preserved.
    """
    index = SpeakerIntervals(intervals)
    for segment in result.get("segments", []):
        segment.pop("speaker", None)
        scores = index.candidates(segment.get("start", 0), segment.get("end", 0))
        if scores:
            segment["speaker"] = max(scores, key=scores.get)
        for word in segment.get("words", []):
            word.pop("speaker", None)
            word.pop("speaker_review", None)
            word.pop("speaker_candidates", None)
            if "start" not in word:
                word["speaker_review"] = "missing_timing"
                continue
            scores = index.candidates(word["start"], word.get("end", word["start"]))
            if scores:
                word["speaker"] = max(scores, key=scores.get)
                if len(scores) > 1:
                    word["speaker_review"] = "multiple_speakers"
                    word["speaker_candidates"] = scores
            else:
                word["speaker_review"] = "no_speaker_interval"
    return result


def review_reasons(segment):
    if "original_speaker" in segment:
        return []  # A manual assignment supersedes the automatic review suggestion.
    reasons = {w["speaker_review"] for w in segment.get("words", []) if w.get("speaker_review")}
    if not segment.get("speaker"):
        reasons.add("no_speaker_interval")
    labels = {
        "multiple_speakers": "Some word timings match more than one speaker. Listen before accepting the assignment.",
        "missing_timing": "Some words have no aligned timing, so their speaker could not be assigned.",
        "no_speaker_interval": "No speaker interval matches some or all of this turn.",
    }
    return [labels[reason] for reason in sorted(reasons) if reason in labels]
