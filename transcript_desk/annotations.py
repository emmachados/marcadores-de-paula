"""Annotations stored with the project, with stable category and turn identifiers."""
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import uuid
from pathlib import Path

from transcript_desk.codebook import (LABEL_PRESETS, FUNCTION_PRESETS, DEFAULT_FORMS,
                                      ensure_codebook, resolve_entry, annotation_name, new_entry)

SCHEMA = 2
LABELS = tuple(item[0] for item in LABEL_PRESETS)
FUNCTIONS = tuple(item[0] for item in FUNCTION_PRESETS)
FORMS = DEFAULT_FORMS
STATUSES = ("suggested", "confirmed", "rejected", "needs review")
CERTAINTIES = ("Unspecified", "Certain", "Tentative", "Uncertain")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def touch_annotation(annotation):
    annotation["modified_at"] = now_iso()


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_turn_ids(result):
    """Assign identities once; retain existing identities and repair split duplicates."""
    seen = set()
    for segment in result.get("segments", []):
        identity = segment.get("turn_id")
        if not isinstance(identity, str) or not identity or identity in seen:
            identity = uuid.uuid4().hex
            segment["turn_id"] = identity
        seen.add(identity)


def sync_annotation_turn_ids(result):
    """Call after legacy numeric indices have been remapped by a structural edit."""
    ensure_turn_ids(result)
    segments = result.get("segments", [])
    by_id = {s["turn_id"]: i for i, s in enumerate(segments)}
    for a in result.get("annotations", []):
        row = a.get("segment")
        if a.get("spans"):
            if not isinstance(a["spans"], list) or not all(isinstance(span, dict) and isinstance(span.get("turn_id"), str) for span in a["spans"]):
                raise ValueError("Invalid annotation spans")
            first = a["spans"][0]
            if first.get("turn_id") in by_id:
                a["segment"] = row = by_id[first["turn_id"]]
            else:
                a["status"] = "needs review"
        if type(row) is int and 0 <= row < len(segments):
            a["turn_id"] = segments[row]["turn_id"]


def migrate_annotations(result):
    annotations = result.get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError("Invalid annotation list")
    if not annotations and "annotation_codebook" not in result:
        return
    book = ensure_codebook(result)
    if annotations:
        ensure_turn_ids(result)
    for a in annotations:
        if not isinstance(a, dict):
            raise ValueError("Invalid annotation structure")
        for field, kind in (("label", "labels"), ("function", "functions")):
            value = a.get(field + "_id", a.get(field))
            if not isinstance(value, str):
                raise ValueError("Invalid annotation category")
            entry = resolve_entry(result, kind, value)
            if entry is None:
                entry = resolve_entry(result, kind, a.get(field, ""))
            if entry is None:
                # A historical category remains readable even if absent from a preset.
                name = a.get(field, value)
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Invalid annotation category")
                entry = new_entry(name)
                if field + "_id" in a:
                    entry["id"] = value
                book[kind].append(entry)
            a[field + "_id"] = entry["id"]
            a[field] = entry["name"]
        a.setdefault("anchor_type", "text")
        a.setdefault("certainty", "Unspecified")
        a.setdefault("author", "")
        # Historical creation dates are unknown and stay empty after migration.
        a.setdefault("created_at", "")
        a.setdefault("modified_at", "")
    sync_annotation_turn_ids(result)
    result["annotation_schema"] = SCHEMA


def _category(result, label):
    ensure_codebook(result)
    entry = resolve_entry(result, "labels", label)
    if entry is None:
        raise ValueError("Choose a label from this project's codebook.")
    return entry


def create_annotation(result, row, start_char, end_char, label, *, origin="manual", status="confirmed", author=""):
    segment = result["segments"][row]
    text = segment["text"]
    if not 0 <= start_char < end_char <= len(text):
        raise ValueError("Select a passage and an annotation label.")
    if status not in STATUSES:
        raise ValueError("Invalid annotation status.")
    entry = _category(result, label)
    if not segment.get("turn_id"):
        ensure_turn_ids(result)
    unspecified = resolve_entry(result, "functions", "function:unspecified") or result["annotation_codebook"]["functions"][0]
    created = now_iso()
    return {"id": uuid.uuid4().hex, "segment": row, "turn_id": segment["turn_id"],
            "start_char": start_char, "end_char": end_char,
            "quote": text[start_char:end_char], "text_sha256": fingerprint(text), "label": entry["name"],
            "label_id": entry["id"], "function": unspecified["name"], "function_id": unspecified["id"],
            "status": status, "origin": origin, "note": "", "anchor_type": "text",
            "certainty": "Unspecified", "author": author, "created_at": created, "modified_at": created,
            "start": float(segment["start"]), "end": float(segment["end"]), "timing": "turn"}


def create_time_annotation(result, row, start, end, label, *, author=""):
    if not math.isfinite(start + end) or not 0 <= start < end:
        raise ValueError("End must be later than start, with finite non-negative times.")
    segment = result["segments"][row]
    # An acoustic event uses the same record shape with empty quoted text.
    entry = _category(result, label)
    if not segment.get("turn_id"):
        ensure_turn_ids(result)
    unspecified = resolve_entry(result, "functions", "function:unspecified") or result["annotation_codebook"]["functions"][0]
    created = now_iso()
    return {"id": uuid.uuid4().hex, "segment": row, "turn_id": segment["turn_id"],
            "start_char": 0, "end_char": 0, "quote": "", "text_sha256": "", "anchor_type": "time",
            "label": entry["name"], "label_id": entry["id"], "function": unspecified["name"],
            "function_id": unspecified["id"], "status": "confirmed", "origin": "manual", "note": "",
            "certainty": "Unspecified", "author": author, "created_at": created, "modified_at": created,
            "start": float(start), "end": float(end), "timing": "manual"}


def create_range_annotation(result, first_row, last_row, label, *, author=""):
    if not 0 <= first_row <= last_row < len(result["segments"]):
        raise ValueError("Choose a final turn at or after the first turn.")
    first = result["segments"][first_row]
    if not first["text"]:
        raise ValueError("The first turn is empty; use a time-only annotation.")
    a = create_annotation(result, first_row, 0, len(first["text"]), label, author=author)
    spans = []
    for segment in result["segments"][first_row:last_row + 1]:
        if segment["text"]:
            spans.append({"turn_id": segment["turn_id"], "start_char": 0, "end_char": len(segment["text"]),
                          "quote": segment["text"], "text_sha256": fingerprint(segment["text"])})
    a["spans"] = spans
    a["quote"] = "\n".join(span["quote"] for span in spans)
    a["start"] = min(s["start"] for s in result["segments"][first_row:last_row + 1])
    a["end"] = max(s["end"] for s in result["segments"][first_row:last_row + 1])
    return a


def suggest(result):
    """Find literal candidate forms; the function is left unspecified."""
    migrate_annotations(result)
    book = ensure_codebook(result)
    annotations = result.setdefault("annotations", [])
    existing = {(a["segment"], a["start_char"], a["end_char"], a.get("label_id", a["label"]), a["text_sha256"])
                for a in annotations}
    count = 0
    marker = resolve_entry(result, "labels", "label:discourse-marker")
    repetition = resolve_entry(result, "labels", "label:repetition")
    for row, segment in enumerate(result["segments"]):
        text = segment["text"]
        candidates = []
        if marker and not marker["archived"]:
            for form in book.get("candidate_forms", []):
                pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, form.split())) + r"(?!\w)"
                candidates.extend((m.start(), m.end(), marker["id"]) for m in re.finditer(pattern, text, re.I))
        if repetition and not repetition["archived"]:
            words = list(re.finditer(r"[^\W_]+", text, re.UNICODE))
            for left, right in zip(words, words[1:]):
                if left.group().casefold() == right.group().casefold() and not text[left.end():right.start()].strip():
                    candidates.append((left.start(), right.end(), repetition["id"]))
        for start, end, label in sorted(candidates):
            key = (row, start, end, label, fingerprint(text))
            if key not in existing:
                annotations.append(create_annotation(result, row, start, end, label,
                                   origin="literal-rule-v2", status="suggested"))
                existing.add(key)
                count += 1
    result["annotation_schema"] = SCHEMA
    return count


def is_current(annotation, result):
    if annotation.get("anchor_type") == "time":
        return True
    spans = annotation.get("spans")
    if spans:
        by_id = {s.get("turn_id"): s for s in result["segments"]}
        for span in spans:
            segment = by_id.get(span.get("turn_id"))
            if not segment:
                return False
            text = segment["text"]
            if (span.get("text_sha256") != fingerprint(text) or
                    text[span["start_char"]:span["end_char"]] != span["quote"]):
                return False
        return True
    row = annotation["segment"]
    if not 0 <= row < len(result["segments"]):
        return False
    text = result["segments"][row]["text"]
    return (annotation["text_sha256"] == fingerprint(text) and
            text[annotation["start_char"]:annotation["end_char"]] == annotation["quote"])


def invalidate(result, row):
    turn_id = result["segments"][row].get("turn_id")
    for annotation in result.get("annotations", []):
        affected = annotation["segment"] == row or any(s.get("turn_id") == turn_id for s in annotation.get("spans", []))
        if affected and not is_current(annotation, result):
            if annotation["status"] != "needs review":
                annotation["previous_status"] = annotation["status"]
            annotation["status"] = "needs review"
            touch_annotation(annotation)


def duplicate_annotation(annotation):
    duplicated = copy.deepcopy(annotation)
    duplicated["id"] = uuid.uuid4().hex
    duplicated.pop("related_ids", None)
    duplicated["created_at"] = duplicated["modified_at"] = now_iso()
    return duplicated


def validate_annotations(result):
    migrate_annotations(result)
    ids = set()
    for a in result.get("annotations", []):
        try:
            if not isinstance(a, dict) or a["id"] in ids:
                raise ValueError("Invalid or duplicate annotation")
            ids.add(a["id"])
            row, start, end = a["segment"], a["start_char"], a["end_char"]
            if any(type(v) is not int for v in (row, start, end)) or not 0 <= row < len(result["segments"]):
                raise ValueError("Invalid annotation anchor")
            anchor_type = a.get("anchor_type", "text")
            if anchor_type not in ("text", "time") or (anchor_type == "text" and not 0 <= start < end) or (anchor_type == "time" and (start != 0 or end != 0)):
                raise ValueError("Invalid annotation range")
            if a["status"] not in STATUSES or not resolve_entry(result, "labels", a["label_id"]):
                raise ValueError("Invalid annotation label or status")
            if not resolve_entry(result, "functions", a["function_id"]) or a["timing"] not in ("turn", "manual"):
                raise ValueError("Invalid annotation function or timing")
            if a["certainty"] not in CERTAINTIES:
                raise ValueError("Invalid annotation certainty")
            related = a.get("related_ids", [])
            if not isinstance(related, list) or any(not isinstance(identity, str) or not identity or identity == a["id"] for identity in related) or len(set(related)) != len(related):
                raise ValueError("Invalid related annotation identifiers")
            for field in ("id", "quote", "text_sha256", "origin", "note", "author", "created_at", "modified_at"):
                if not isinstance(a[field], str):
                    raise ValueError("Invalid annotation text")
            spans = a.get("spans", [])
            if not isinstance(spans, list):
                raise ValueError("Invalid annotation spans")
            for span in spans:
                if (not isinstance(span, dict) or any(not isinstance(span.get(k), str) for k in ("turn_id", "quote", "text_sha256")) or
                        any(type(span.get(k)) is not int for k in ("start_char", "end_char")) or not 0 <= span["start_char"] < span["end_char"]):
                    raise ValueError("Invalid annotation span")
            times = float(a["start"]), float(a["end"])
            if not math.isfinite(sum(times)) or not 0 <= times[0] < times[1]:
                raise ValueError("Invalid annotation times")
            if not is_current(a, result) and a["status"] != "needs review":
                raise ValueError("Changed text requires annotation review")
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError("Invalid annotation structure") from exc


def export_annotations(result, folder, *, statuses=None, selected_ids=None):
    """Keep category definitions and provenance with filtered or complete sidecars."""
    validate_annotations(result)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    annotations = [a for a in result.get("annotations", []) if
                   (statuses is None or a["status"] in statuses) and (selected_ids is None or a["id"] in selected_ids)]
    payload = {"schema": SCHEMA, "source": result.get("metadata", {}),
               "convention": "Labels from the project codebook, assigned by hand.",
               "codebook": result.get("annotation_codebook"), "annotations": annotations}
    (folder / "annotations.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["id", "segment", "turn_id", "start", "end", "timing", "anchor_type", "start_char", "end_char", "quote",
              "label_id", "label", "function_id", "function", "status", "certainty", "author", "created_at", "modified_at", "origin", "note", "spans", "related_ids"]
    with (folder / "annotations.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for a in annotations:
            values = [json.dumps(a[k], ensure_ascii=False) if k in ("spans", "related_ids") and k in a else a.get(k, "") for k in fields]
            writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
    return [folder / "annotations.json", folder / "annotations.csv"]
