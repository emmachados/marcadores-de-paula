import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_release_model import verify
from transcript_desk.model_pack import inspect_pack


def test_model_pack_requires_provenance(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        inspect_pack(tmp_path)
    for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
        (tmp_path / name).write_text("{}")
    metadata = {"schema": 1, "name": "Pilot", "base_model": "large-v3-turbo", "language": "es",
                "training_corpus": "Fixture corpus", "evaluation": {"status": "research only"}}
    (tmp_path / "paula-model.json").write_text(json.dumps(metadata))
    assert inspect_pack(tmp_path)[1] == metadata


def test_release_model_verifier_follows_its_manifest(tmp_path):
    weights = b"fixture weights"
    model_sha256 = hashlib.sha256(weights).hexdigest()
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        packed.writestr("example-pack/model.bin", weights)
        packed.writestr("example-pack/paula-model.json", json.dumps(
            {"name": "Example", "language": "es", "model_sha256": model_sha256}))
    manifest = {"edition": "example", "archive": archive.name, "directory": "example-pack", "name": "Example",
                "language": "es", "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "model_sha256": model_sha256}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    verify(archive, path)
    path.write_text(json.dumps({**manifest, "name": "Other"}))
    with pytest.raises(ValueError, match="Unexpected recognition model"):
        verify(archive, path)
    path.write_text(json.dumps({**manifest, "archive_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="checksum"):
        verify(archive, path)
