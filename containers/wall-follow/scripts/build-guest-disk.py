"""Populate a sparse partitioned disk without host mounts or loop devices."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--uid", type=int, required=True)
parser.add_argument("--gid", type=int, required=True)
args = parser.parse_args()
work = Path("/work")
policy = json.loads((work / "guest-policy.json").read_text())
size = policy["root_size_mib"] * 1024 * 1024
disk_id = int(policy["disk_identifier"], 16)
offset = 2 * 1024 * 1024
with tempfile.TemporaryDirectory(prefix="guest-root-") as temporary:
    root = Path(temporary)
    run("tar", "--numeric-owner", "-xf", str(work / "rootfs.tar"), "-C", str(root))
    (root / "etc/hosts").write_text("127.0.0.1 localhost chimaera-guest\n::1 localhost\n")
    (root / "etc/hostname").write_text("chimaera-guest\n")
    (root / "etc/resolv.conf").write_text("")
    filesystem = work / "root.ext4"
    with filesystem.open("wb") as stream:
        stream.truncate(size)
    print(run("mke2fs", "-F", "-t", "ext4", "-m", "0", "-L", "chimaera-root",
              "-U", policy["filesystem_uuid"], "-d", str(root), str(filesystem)), flush=True)
    check = subprocess.run(["e2fsck", "-f", "-n", str(filesystem)], capture_output=True, text=True)
    if check.returncode:
        raise RuntimeError(check.stdout + check.stderr)
    required = ("/sbin/init", "/usr/local/bin/chimaera_wall_follow_controller",
                "/usr/local/bin/chimaera_wall_follow_bridge", "/usr/local/bin/chimaera_wall_follow_guest",
                "/usr/local/bin/m5", "/opt/ros/humble/setup.bash")
    inspected = {}
    for name in required:
        actual = "/usr/sbin/init" if name == "/sbin/init" else name
        result = run("debugfs", "-R", f"stat {actual}", str(filesystem))
        if "User:     0" not in result or "Inode:" not in result:
            raise RuntimeError(f"Missing or nonroot guest file {name}: {result}")
        inspected[name] = result
    mbr = bytearray(512)
    struct.pack_into("<I", mbr, 440, disk_id)
    # Partition 1 reserves one MiB; root partition 2 starts at two MiB.
    for index, start, count in ((0, 2048, 2048), (1, offset // 512, size // 512)):
        struct.pack_into("<B3sB3sII", mbr, 446 + index * 16, 0,
                         b"\xfe\xff\xff", 0x83, b"\xfe\xff\xff", start, count)
    mbr[510:512] = b"\x55\xaa"
    disk = work / "disk.img"
    with disk.open("wb") as output, filesystem.open("rb") as source:
        output.write(mbr)
        output.truncate(offset + size)
        current = 0
        while current < size:
            try:
                start = os.lseek(source.fileno(), current, os.SEEK_DATA)
            except OSError as error:
                if error.errno == 6:  # ENXIO: only a sparse hole remains.
                    break
                raise
            end = min(os.lseek(source.fileno(), start, os.SEEK_HOLE), size)
            source.seek(start)
            output.seek(offset + start)
            while start < end:
                block = source.read(min(1024 * 1024, end - start))
                if not block:
                    raise RuntimeError("Unexpected filesystem EOF")
                output.write(block)
                start += len(block)
            current = end
        output.flush()
        os.fsync(output.fileno())
    metadata = root / "usr/local/share/chimaera"
    for name in ("payload.json", "sdk-build-manifest.json"):
        shutil.copy2(metadata / name, work / name)
    shutil.copy2(root / "opt/chimaera/runtime-packages.tsv", work / "packages.tsv")
    manifest = json.loads((work / "inputs.json").read_text())
    manifest.update(schema_version=1,
                    layout={"table": "dos", "disk_identifier": policy["disk_identifier"],
                            "root_partition": 2, "root_partuuid": f"{disk_id:08x}-02",
                            "root_offset_bytes": offset, "root_size_bytes": size,
                            "filesystem_uuid": policy["filesystem_uuid"]},
                    sha256={name: sha256(work / name) for name in ("disk.img", "kernel")},
                    payload_sha256=sha256(work / "payload.json"),
                    system_packages_sha256=sha256(work / "packages.tsv"),
                    filesystem_tool=run("dpkg-query", "-W", "-f=${Version}", "e2fsprogs"),
                    filesystem_features=run("dumpe2fs", "-h", str(filesystem)))
    manifest["layout"]["kernel_root_argument"] = "root=PARTUUID=" + manifest["layout"]["root_partuuid"]
    (work / "inspection.json").write_text(json.dumps({"files": inspected, "e2fsck": check.stdout + check.stderr}, indent=2) + "\n")
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    filesystem.unlink()
for path in work.iterdir():
    if path.is_file():
        os.chown(path, args.uid, args.gid)
print(f"Created {disk.stat().st_size} byte sparse guest disk; SHA256 {manifest['sha256']['disk.img']}", flush=True)
