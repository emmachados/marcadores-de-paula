"""Reject a model archive that does not match its manifest before bundling it in a package."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def verify(path, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["archive_sha256"]:
        raise ValueError("Model archive checksum mismatch")
    with zipfile.ZipFile(path) as archive:
        prefix = manifest["directory"] + "/"
        for name in archive.namelist():
            if not name.startswith(prefix) or ".." in Path(name).parts or "\\" in name:
                raise ValueError("Unexpected model archive path")
        with archive.open(prefix + "model.bin") as stream:
            model_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        metadata = json.loads(archive.read(prefix + "paula-model.json"))
        if model_digest != manifest["model_sha256"] or model_digest != metadata["model_sha256"]:
            raise ValueError("Model weights checksum mismatch")
        if metadata["name"] != manifest["name"] or metadata["language"] != manifest["language"]:
            raise ValueError("Unexpected recognition model")
    print(f"{manifest['name']} archive and weights verified.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), sys.argv[2])
