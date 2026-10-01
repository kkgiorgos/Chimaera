"""Record resolved packages, build inputs, and artifact checksums."""

import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


root = Path("/opt/chimaera")
robot = Path("/src/ros/wall_follow_ws/src/wall_follow_robot")
artifacts = sorted(path for tree in ("gem5", "ros", "licenses")
                   for path in (root / tree).rglob("*") if path.is_file() and not path.is_symlink())
symlinks = sorted(path for tree in ("gem5", "ros", "licenses")
                  for path in (root / tree).rglob("*") if path.is_symlink())
manifest = {
    "schema_version": 1,
    "profile": json.loads((root / "profile.json").read_text()),
    "source_revision": sys.argv[1],
    "source_tree_sha256": sys.argv[2],
    "working_tree_dirty": sys.argv[3] == "1",
    "controller_source_sha256": {
        str(path.relative_to(robot)): sha256(path)
        for path in sorted(robot.rglob("*")) if path.is_file()
    },
    "artifact_sha256": {str(path.relative_to(root)): sha256(path) for path in artifacts},
    "artifact_modes": {str(path.relative_to(root)): stat.S_IMODE(path.stat().st_mode) for path in artifacts},
    "artifact_symlinks": {str(path.relative_to(root)): str(path.readlink()) for path in symlinks},
    "packages_sha256": sha256(root / "packages.tsv"),
    "profile_sha256": sha256(root / "profile.json"),
    "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
    "python": subprocess.check_output(["python3", "--version"], text=True).strip(),
}
(root / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
