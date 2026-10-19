"""Optional CTranslate2 model packs, kept apart from the stock models."""
import json
from pathlib import Path


def inspect_pack(directory):
    folder = Path(directory).expanduser().resolve()
    for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json", "paula-model.json"):
        if not (folder / name).is_file():
            raise ValueError(f"The model pack is missing {name}.")
    metadata = json.loads((folder / "paula-model.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("The model pack metadata must be an object.")
    if metadata.get("schema") != 1 or metadata.get("base_model") not in ("large-v3", "large-v3-turbo"):
        raise ValueError("This is not a supported Marcadores de Paula model pack.")
    if metadata.get("language") != "es" or not isinstance(metadata.get("name"), str) or not metadata["name"].strip():
        raise ValueError("The model pack must declare its name and Spanish language.")
    if not isinstance(metadata.get("evaluation"), dict) or not metadata.get("training_corpus"):
        raise ValueError("The model pack must include its training corpus and evaluation record.")
    return folder, metadata
