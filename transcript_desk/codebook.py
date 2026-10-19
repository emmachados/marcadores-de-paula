"""Project-local annotation vocabulary with identities independent of display names."""
import copy
import json
import re
import uuid
from pathlib import Path


LABEL_PRESETS = (
    ("Discourse marker", "A passage selected for discourse-marker analysis; its function requires review.", "#4769B0"),
    ("Repetition", "Repeated wording, without assuming its cause or discourse function.", "#7962A8"),
    ("Restart / unfinished word", "An abandoned word or construction followed by a restart.", "#A46937"),
    ("Filled pause", "A vocalised hesitation such as um or eh.", "#9A7532"),
    ("Pause", "An audible interval without speech; refine its times manually.", "#637787"),
    ("Overlap", "Simultaneous speech verified against the recording.", "#B55365"),
    ("Laughter", "Audible laughter or a passage produced while laughing.", "#387F72"),
    ("Lengthening", "A perceptibly prolonged speech sound.", "#8C69A1"),
    ("Uncertain speech", "Wording or interpretation that still requires listening.", "#AE653D"),
    ("Other", "A project-specific observation not covered by another label.", "#737373"),
    ("Breath", "An audible inhalation or exhalation.", "#5B8391"),
    ("Cough / throat clearing", "An audible cough or throat-clearing event.", "#587F70"),
    ("Background sound", "An audible event outside the speech being transcribed.", "#697D8D"),
    ("Unintelligible passage", "Speech is audible but its wording cannot be established.", "#AD5C50"),
    ("Self-correction", "A speaker revises wording within their own contribution.", "#8A67AB"),
    ("Other-correction", "A participant corrects another participant's contribution.", "#966080"),
    ("Self-repetition", "A speaker repeats their own preceding wording.", "#7962A8"),
    ("Other-repetition", "A speaker repeats wording from another participant.", "#886D9E"),
    ("Interruption", "An annotator identifies an interruption of a contribution, after checking the recording.", "#B55365"),
    ("Reported speech / quotation", "A passage represented as speech or thought from another occasion.", "#596CAF"),
    ("Language change", "A passage containing a change of language or language variety.", "#338279"),
    ("Emphasis", "Perceptual prominence marked by the annotator.", "#A56F37"),
    ("Intonation", "A passage selected for intonational description in the note.", "#6675A7"),
    ("Quiet / whispered speech", "Audibly quiet or whispered speech.", "#627D91"),
    ("Loud speech", "Audibly raised voice, with its interpretation left to the annotator.", "#A65A5A"),
    ("Response / acknowledgement", "A response or acknowledgement whose function can be coded separately.", "#428470"),
    ("Topic boundary", "A proposed boundary between conversational topics.", "#87669C"),
)

FUNCTION_PRESETS = (
    ("Unspecified", "No discourse function has been assigned."),
    ("Reformulation", "A contribution reformulates preceding discourse."),
    ("Discourse organisation", "An element organises the unfolding discourse."),
    ("Interaction management", "An element manages participation or interaction."),
    ("Stance / modality", "An element expresses stance or modality."),
    ("Other", "A function described in the annotation note."),
    ("Explanation / clarification", "A passage explains or clarifies another contribution."),
    ("Correction", "A passage corrects a preceding formulation."),
    ("Conclusion / consequence", "A passage presents a conclusion or consequence."),
    ("Contrast / counterargument", "A passage contrasts with or challenges another contribution."),
    ("Topic introduction", "A passage introduces a topic."),
    ("Topic continuation / return", "A passage continues or resumes a topic."),
    ("Turn taking / holding", "An element claims or retains a speaking turn."),
    ("Turn yielding", "An element offers the turn to another participant."),
    ("Agreement", "A passage expresses agreement."),
    ("Disagreement", "A passage expresses disagreement."),
    ("Understanding / acknowledgement", "A passage signals reception or understanding."),
    ("Mitigation", "An element reduces the force of a contribution."),
    ("Intensification", "An element increases the force of a contribution."),
    ("Hesitation / planning", "An element is interpreted as hesitation or planning."),
    ("Attention / address", "An element directs attention or addresses a participant."),
)

DEFAULT_FORMS = ("pues", "bueno", "claro", "vale", "entonces", "hombre", "eh", "ah", "mm",
                 "o sea", "es decir", "en fin", "a ver")


def builtin_id(kind, name):
    return kind + ":" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def default_codebook():
    return {"schema": 1,
            "labels": [{"id": builtin_id("label", name), "name": name, "definition": definition,
                        "color": color, "shortcut": "", "archived": False, "group": "", "favourite": False}
                       for name, definition, color in LABEL_PRESETS],
            "functions": [{"id": builtin_id("function", name), "name": name, "definition": definition,
                           "color": "#737373", "shortcut": "", "archived": False, "group": "", "favourite": False}
                          for name, definition in FUNCTION_PRESETS],
            "candidate_forms": list(DEFAULT_FORMS)}


def validate_codebook(book):
    if not isinstance(book, dict) or book.get("schema") != 1:
        raise ValueError("Unsupported annotation codebook schema.")
    for kind in ("labels", "functions"):
        entries = book.get(kind)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 2000:
            raise ValueError(f"The codebook needs between 1 and 2,000 {kind}.")
        identities, names, shortcuts = set(), set(), set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid codebook entry.")
            for key in ("id", "name", "definition", "color", "shortcut"):
                if not isinstance(entry.get(key), str):
                    raise ValueError(f"Invalid codebook {key}.")
            if (not entry["id"] or len(entry["id"]) > 128 or entry["id"] in identities or
                    not entry["name"].strip() or len(entry["name"]) > 160 or entry["name"].casefold() in names):
                raise ValueError("Codebook names and identifiers must be nonempty and unique in each category.")
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", entry["color"]):
                raise ValueError("Use a six-digit hexadecimal colour, for example #4769B0.")
            if type(entry.get("archived")) is not bool:
                raise ValueError("Invalid archived flag.")
            if not isinstance(entry.get("group", ""), str) or type(entry.get("favourite", False)) is not bool:
                raise ValueError("Invalid category group or favourite flag.")
            shortcut = entry["shortcut"].strip().casefold()
            if len(shortcut) > 64 or (shortcut and shortcut in shortcuts and not entry["archived"]):
                raise ValueError("Active shortcuts must be unique within each category.")
            identities.add(entry["id"])
            names.add(entry["name"].casefold())
            if shortcut and not entry["archived"]:
                shortcuts.add(shortcut)
    forms = book.get("candidate_forms", [])
    if not isinstance(forms, list) or len(forms) > 2000 or any(not isinstance(f, str) or not f.strip() or len(f) > 200 for f in forms):
        raise ValueError("Candidate forms must be nonempty strings of at most 200 characters.")
    return book


def ensure_codebook(result):
    if "annotation_codebook" not in result:
        result["annotation_codebook"] = default_codebook()
    return validate_codebook(result["annotation_codebook"])


def entries(result, kind, active_only=False):
    book = result.get("annotation_codebook") or default_codebook()
    return sorted([entry for entry in book[kind] if not active_only or not entry["archived"]],
                  key=lambda entry: not entry.get("favourite", False))


def resolve_entry(result, kind, value):
    return next((entry for entry in entries(result, kind) if entry["id"] == value or entry["name"] == value), None)


def annotation_name(result, annotation, kind="labels"):
    field = "label" if kind == "labels" else "function"
    entry = resolve_entry(result, kind, annotation.get(field + "_id", annotation.get(field, "")))
    return entry["name"] if entry else annotation.get(field, "")


def new_entry(name="New label"):
    return {"id": "custom:" + uuid.uuid4().hex, "name": name, "definition": "", "color": "#737373",
            "shortcut": "", "archived": False, "group": "", "favourite": False}


def merge_codebook(current, imported):
    """Merge by stable identity, preserving categories used by the current project."""
    merged = copy.deepcopy(validate_codebook(current))
    validate_codebook(imported)
    for kind in ("labels", "functions"):
        by_id = {entry["id"]: entry for entry in merged[kind]}
        for incoming in imported[kind]:
            if incoming["id"] in by_id:
                by_id[incoming["id"]].update(copy.deepcopy(incoming))
            else:
                merged[kind].append(copy.deepcopy(incoming))
    merged["candidate_forms"] = list(dict.fromkeys(imported.get("candidate_forms", [])))
    return validate_codebook(merged)


def read_codebook(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("The codebook file exceeds 4 MB.")
    return validate_codebook(json.loads(path.read_text(encoding="utf-8")))


def write_codebook(path, book):
    Path(path).write_text(json.dumps(validate_codebook(book), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
