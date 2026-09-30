"""Read the completed guest artifact at the worker boundary."""

import hashlib
import json
from pathlib import Path
import re


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(directory):
    directory = Path(directory).expanduser().resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported guest manifest version")
    layout = manifest["layout"]
    if layout["root_partition"] != 2 or not re.fullmatch(r"[0-9a-f]{8}-02", layout["root_partuuid"]):
        raise ValueError("Guest manifest has an unsupported root layout")
    for name in ("disk.img", "kernel"):
        path = directory / name
        expected = manifest["sha256"][name]
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid guest artifact: {path}")
        if sha256(path) != expected:
            raise ValueError(f"Guest artifact checksum differs: {path}")
    return manifest
