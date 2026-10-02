#!/usr/bin/env python3
"""Build the shared Docker image and guest needed for wall-follow benchmarks."""

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys


def build_input_digest(root, directory):
    """Hash the copied trees using this recipe's Docker ignore rules."""
    rules = [line.strip() for line in (directory / "Dockerfile.dockerignore").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    seen_exclusion = False
    for rule in rules:
        if rule.startswith("!"):
            if seen_exclusion or not rule.endswith(("/", "/**")):
                raise ValueError(f"Unsupported Docker inclusion order or syntax: {rule}")
        elif rule != "**":
            seen_exclusion = True
    trees = [rule[1:-3] for rule in rules if rule.startswith("!") and rule.endswith("/**")]
    exclusions = [rule for rule in rules if rule and not rule.startswith(("!", "#")) and rule != "**"]
    for rule in exclusions:
        pattern = rule[3:] if rule.startswith("**/") else rule
        unsupported = ("/" in pattern or "[" in pattern) if rule.startswith("**/") else any(c in pattern for c in "*?[")
        if unsupported:
            raise ValueError(f"Unsupported Docker ignore rule for input hashing: {rule}")

    def excluded(path):
        relative = path.relative_to(root)
        return any(
            any(fnmatch.fnmatchcase(part, rule[3:]) for part in relative.parts)
            if rule.startswith("**/") else
            relative.as_posix() == rule or relative.as_posix().startswith(rule + "/")
            for rule in exclusions
        )

    paths = set()
    for tree in trees:
        base = root / tree
        if not base.exists():
            continue
        paths.add(base)
        for current, directories, files in os.walk(base, followlinks=False):
            parent = Path(current)
            directories[:] = [name for name in directories if not excluded(parent / name)]
            paths.update(parent / name for name in directories)
            paths.update(parent / name for name in files if not excluded(parent / name))
    digest = hashlib.sha256()
    for path in sorted(paths):
        mode = path.lstat().st_mode
        if path.is_symlink():
            content = os.fsencode(path.readlink())
        elif path.is_dir():
            content = b""
        elif stat.S_ISREG(mode):
            content = path.read_bytes()
        else:
            raise ValueError(f"Unsupported file type in Docker context: {path}")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(str(mode).encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def main():
    directory = Path(__file__).resolve().parent
    root = directory.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="jammy-humble-fortress")
    parser.add_argument("--target", choices=("all", "build-env", "builder", "artifacts", "worker"), default="all",
                        help="all prepares the image and guest (default); select a stage for development")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--tag", help="override the tag for an individual image target")
    parser.add_argument("--output", type=Path, default=directory / "artifacts",
                        help="export directory for --target artifacts")
    parser.add_argument("--print-command", action="store_true")
    parser.add_argument("--no-cache", action="store_true",
                        help="refresh package versions by rebuilding Docker layers")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.target == "all" and args.tag:
        parser.error("--tag requires an individual image target")
    profile_path = directory / "profiles" / f"{args.profile}.json"
    if profile_path.parent != directory / "profiles" or not profile_path.is_file():
        parser.error(f"Unknown profile: {args.profile}")
    profile = json.loads(profile_path.read_text())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    digest = build_input_digest(root, directory)
    target = "worker" if args.target == "all" else args.target
    command = [
        "docker", "buildx", "build", "--progress", "plain", "--provenance=false",
        "--platform", profile["platform"], "--target", target,
        "--file", str(directory / "Dockerfile"),
        "--build-arg", f"BASE_IMAGE={profile['base_image']}",
        "--build-arg", f"STACK_PROFILE={profile['id']}",
        "--build-arg", f"ROS_DISTRO={profile['ros_distro']}",
        "--build-arg", f"BUILD_JOBS={args.jobs}",
        "--build-arg", f"SOURCE_REVISION={revision}",
        "--build-arg", f"SOURCE_TREE_SHA256={digest}",
        "--build-arg", f"SOURCE_DIRTY={int(dirty)}",
        "--label", f"io.chimaera.stack={profile['id']}",
    ]
    if args.no_cache:
        command.append("--no-cache")
    if target == "artifacts":
        output = args.output.resolve()
        if not args.print_command and output.exists():
            if not output.is_dir() or any(output.iterdir()):
                parser.error("Artifact output must be an empty directory; choose a new --output")
        command += ["--output", f"type=local,dest={output}"]
    else:
        command += ["--load", "--tag", args.tag or f"chimaera-{target}:{profile['id']}"]
    command.append(str(root))
    print(shlex.join(command), flush=True)
    if not args.print_command:
        subprocess.run(command, check=True)
    if args.target == "all":
        # Keep the SDK tag for prepare-guest.py and existing development commands.
        invocation = ["docker", "tag", f"chimaera-worker:{profile['id']}", f"chimaera-builder:{profile['id']}"]
        print(shlex.join(invocation), flush=True)
        if not args.print_command:
            subprocess.run(invocation, check=True)
        invocation = [sys.executable, str(directory / "prepare-guest.py"), "--profile", args.profile]
        print(shlex.join(invocation), flush=True)
        if not args.print_command:
            subprocess.run(invocation, check=True)
            print("Ready. Run: python3 containers/wall-follow/run-suite.py --output results/demo", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
