"""Read the completed guest artifact at the worker boundary."""

import hashlib
import json
import os
from pathlib import Path
import re


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selection_file(profile):
    if not re.fullmatch(r"[a-z0-9-]+", profile):
        raise ValueError(f"Invalid guest profile: {profile}")
    return Path(__file__).resolve().parent / "guest-assets" / f"{profile}.current.json"


def select(profile, directory, image_id):
    """Publish the default only after immutable guest assets are complete."""
    pointer = selection_file(profile)
    directory = Path(directory).resolve()
    if directory.parent != pointer.parent:
        raise ValueError("Default guest must be inside guest-assets")
    temporary = pointer.with_name(f".{pointer.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"directory": directory.name, "image_id": image_id}) + "\n")
    temporary.replace(pointer)


def selected(profile, image_id=None):
    pointer = selection_file(profile)
    if not pointer.is_file():
        raise ValueError("No prepared guest is selected. Run python3 containers/wall-follow/build.py first, "
                         "or supply --guest-assets DIRECTORY")
    selection = json.loads(pointer.read_text())
    if image_id is not None and selection.get("image_id") != image_id:
        raise ValueError("Selected guest belongs to a different image. Run python3 "
                         "containers/wall-follow/build.py to prepare matching assets, "
                         "or supply --guest-assets DIRECTORY explicitly")
    name = selection.get("directory")
    if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
        raise ValueError(f"Invalid guest selection: {pointer}")
    return (pointer.parent / name).resolve(strict=True)


def verify(directory):
    directory = Path(directory).expanduser().resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported guest manifest version")
    layout = manifest["layout"]
    if layout["root_partition"] != 2 or not re.fullmatch(r"[0-9a-f]{8}-02", layout["root_partuuid"]):
        raise ValueError("Guest manifest has an unsupported root layout")
    payload_path = directory / "payload.json"
    if not payload_path.is_file() or sha256(payload_path) != manifest.get("payload_sha256"):
        raise ValueError("Guest payload inventory is missing or its checksum differs")
    payload = json.loads(payload_path.read_text())
    required = (
        "opt/chimaera/wall_follow/guest_start",
        "opt/chimaera/wall_follow/runtime/guest_bridge",
        "opt/chimaera/wall_follow/runtime/chimaera_ros/runner.py",
        "opt/chimaera/wall_follow/app/lib/wall_follow_robot/controller",
    )
    missing = [name for name in required if name not in payload.get("files", {})]
    if missing:
        raise ValueError(
            "Guest uses an obsolete or incomplete wall-follow runtime; "
            "regenerate it with prepare-guest.py --output NEW_DIRECTORY and "
            "pass that directory to --guest-assets. Missing: " + ", ".join(missing))
    for name in ("disk.img", "kernel"):
        path = directory / name
        expected = manifest["sha256"][name]
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid guest artifact: {path}")
        if sha256(path) != expected:
            raise ValueError(f"Guest artifact checksum differs: {path}")
    return manifest
