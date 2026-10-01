#!/usr/bin/env python3
"""Verify a staged or exported build payload without ROS or gem5 installed."""

import argparse
import hashlib
import json
from pathlib import Path
import stat


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--export", action="store_true", help="Require exactly the exported top-level entries")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads((root / "build-manifest.json").read_text())
    if args.export:
        assert {path.name for path in root.iterdir()} == {
            "gem5", "ros", "licenses", "build-manifest.json", "packages.tsv", "profile.json",
        }, "Unexpected or missing entries in the export root"
    assert manifest["controller_source_sha256"], "Missing controller source provenance"
    assert len(manifest["source_tree_sha256"]) == 64, "Missing build input digest"
    files = {}
    modes = {}
    symlinks = {}
    for tree in ("gem5", "ros", "licenses"):
        directory = root / tree
        assert directory.is_dir() and not directory.is_symlink(), f"Artifact root must be a real directory: {tree}"
        for path in sorted((root / tree).rglob("*")):
            relative = str(path.relative_to(root))
            if path.is_symlink():
                symlinks[relative] = str(path.readlink())
            elif path.is_file():
                files[relative] = sha256(path)
                modes[relative] = stat.S_IMODE(path.stat().st_mode)
    expected = manifest["artifact_sha256"]
    changed = sorted(path for path in files.keys() | expected.keys()
                     if files.get(path) != expected.get(path))
    assert not changed, f"Artifact files or checksums differ: {changed}"
    assert modes == manifest["artifact_modes"], "Artifact file permissions differ"
    assert symlinks == manifest["artifact_symlinks"], "Artifact symlinks differ"
    for filename, key in (("packages.tsv", "packages_sha256"), ("profile.json", "profile_sha256")):
        assert sha256(root / filename) == manifest[key], f"Checksum differs: {filename}"
    print(f"Verified {len(files)} artifact files and modes, {len(symlinks)} symlinks, profile, and package inventory")


if __name__ == "__main__":
    main()
