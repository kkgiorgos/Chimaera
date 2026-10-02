"""Inventory worker scripts and the SDK payload in the shared image."""

import hashlib
import json
from pathlib import Path
import stat


root = Path("/opt/chimaera")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


profile = json.loads((root / "profile.json").read_text())
paths = set(root.rglob("*")) | set(Path(f"/opt/ros/{profile['ros_distro']}").rglob("*"))
manifest = {"schema_version": 1, "sdk_manifest_sha256": sha256(root / "build-manifest.json"),
            "files": {}, "symlinks": {}}
for path in sorted(paths):
    if path.is_symlink():
        manifest["symlinks"][str(path)] = str(path.readlink())
    elif path.is_file() and path.name != "worker-manifest.json":
        manifest["files"][str(path)] = {"sha256": sha256(path), "mode": stat.S_IMODE(path.stat().st_mode)}
(root / "worker-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
