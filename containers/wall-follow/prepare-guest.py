#!/usr/bin/env python3
"""Construct an immutable guest directory using the verified local Docker SDK."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import urllib.request
import uuid

from guest_assets import select, sha256, verify


def docker(*args, capture=True):
    try:
        return subprocess.run(["docker", *args], check=True, text=True,
                              capture_output=capture).stdout
    except subprocess.CalledProcessError as error:
        if capture:
            raise ValueError(error.stderr.strip() or str(error)) from None
        raise


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="jammy-humble-fortress")
    parser.add_argument("--sdk-image", default="chimaera:jammy-humble-fortress")
    parser.add_argument("--overlay", type=Path, default=directory / "overlay", help="Built benchmark overlay")
    parser.add_argument("--kernel", type=Path, help="Local kernel; otherwise use the gem5 cache or download the pinned resource")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    profile_file = directory.parent / "profiles" / f"{args.profile}.json"
    policy_file = directory / "profiles" / f"{args.profile}.guest.json"
    if profile_file.parent != directory.parent / "profiles" or not policy_file.is_file():
        parser.error("Unknown guest stack profile")
    profile = json.loads(profile_file.read_text())
    policy = json.loads(policy_file.read_text())
    if profile["platform"] != "linux/amd64" or policy["construction"] != "ubuntu-docker-rootfs":
        parser.error("Unsupported guest platform or construction method")
    image = json.loads(docker("image", "inspect", args.sdk_image))[0]
    if image["Architecture"] != "amd64" or image["Os"] != "linux":
        parser.error("Guest SDK must be linux/amd64")
    sdk_id = image["Id"]
    overlay = args.overlay.expanduser().resolve(strict=True)
    if not (overlay / "share/wall_follow_bridge/config/session.json").is_file():
        parser.error("Build the benchmark overlay with build-overlay.py first")
    overlay_hashes = {str(p.relative_to(overlay)): sha256(p) for p in sorted(overlay.rglob("*")) if p.is_file()}
    build_files = [directory / "Guest.Dockerfile", directory / "Guest.Dockerfile.dockerignore",
                   directory / "prepare-guest.py", directory / "guest_assets.py", profile_file, policy_file,
                   *[directory / "scripts" / name for name in
                     ("stage-guest.py", "guest_session.py", "install-runtime.py", "guest-init.sh", "build-guest-disk.py")]]
    source_hashes = {str(p): sha256(p) for p in build_files}
    identity = {"profile": args.profile, "sdk_image_id": sdk_id, "ubuntu_image": profile["base_image"],
                "kernel": policy["kernel"], "construction_sha256": source_hashes, "overlay_sha256": overlay_hashes}
    input_sha256 = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = (args.output or directory / "guest-assets" / f"{args.profile}-{input_sha256[:16]}").expanduser().resolve()
    if "," in str(output):
        parser.error("Docker bind paths cannot contain commas")
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / f".{output.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if output.exists():
            manifest = verify(output)
            if manifest["input_sha256"] != input_sha256:
                parser.error("Existing guest has different construction inputs; choose a new --output")
            if not args.output:
                select(args.profile, output, sdk_id)
            print(f"Verified existing guest: {output}")
            return
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.build-", dir=output.parent))
        token = uuid.uuid4().hex
        alias = "chimaera-guest-sdk:" + token
        image_ids = {}
        names = (f"chimaera-guest-export-{token}", f"chimaera-guest-disk-{token}")
        try:
            kernel = args.kernel or Path.home() / ".cache/gem5" / policy["kernel"]["resource"]
            if kernel.is_file():
                if sha256(kernel) != policy["kernel"]["sha256"]:
                    raise ValueError(f"Kernel differs from pinned resource: {kernel}")
                shutil.copy2(kernel, stage / "kernel")
            elif args.kernel:
                raise ValueError(f"Missing kernel: {kernel}")
            else:
                print("Downloading pinned guest kernel", flush=True)
                with urllib.request.urlopen(policy["kernel"]["url"], timeout=60) as source, (stage / "kernel").open("wb") as target:
                    shutil.copyfileobj(source, target)
                if sha256(stage / "kernel") != policy["kernel"]["sha256"]:
                    raise ValueError("Downloaded kernel checksum differs")
            docker("tag", sdk_id, alias)
            for target in ("rootfs", "tools"):
                iid = stage / f"{target}.iid"
                docker("buildx", "build", "--load", "--platform", profile["platform"],
                       "--progress", "plain", "--file", str(directory / "Guest.Dockerfile"),
                       "--target", target, "--iidfile", str(iid),
                       "--build-context", f"benchmark={overlay}",
                       "--build-context", f"profiles={directory.parent / 'profiles'}",
                       "--build-arg", f"SDK_IMAGE={alias}", "--build-arg", f"BASE_IMAGE={profile['base_image']}",
                       "--build-arg", f"STACK_PROFILE={args.profile}", str(directory), capture=False)
                image_ids[target] = iid.read_text().strip()
            docker("create", "--name", names[0], image_ids["rootfs"])
            docker("export", "--output", str(stage / "rootfs.tar"), names[0], capture=False)
            shutil.copy2(policy_file, stage / "guest-policy.json")
            inputs = {**identity, "input_sha256": input_sha256, "construction_images": image_ids,
                      "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip(),
                      "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=directory, text=True)),
                      "created_at": datetime.now(timezone.utc).isoformat()}
            (stage / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
            docker("run", "--rm", "--name", names[1], "--network", "none",
                   "--mount", f"type=bind,source={stage},target=/work", image_ids["tools"],
                   "--uid", str(os.getuid()), "--gid", str(os.getgid()), capture=False)
            manifest = verify(stage)
            for name in ("rootfs.tar", "guest-policy.json", "inputs.json", "rootfs.iid", "tools.iid"):
                (stage / name).unlink()
            for path in stage.iterdir():
                path.chmod(0o444)
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            stage.chmod(0o555)
            stage.rename(output)
            fd = os.open(output.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            if not args.output:
                select(args.profile, output, sdk_id)
            print(f"Guest assets: {output}", flush=True)
            print(f"Disk SHA256: {manifest['sha256']['disk.img']}", flush=True)
        finally:
            for name in names:
                subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Construction images and temporary SDK tags are not runtime inputs.
            for image_id in set(image_ids.values()):
                subprocess.run(["docker", "image", "rm", image_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "image", "rm", alias], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if stage.exists():
                stage.chmod(0o755)
                shutil.rmtree(stage)


if __name__ == "__main__":
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
