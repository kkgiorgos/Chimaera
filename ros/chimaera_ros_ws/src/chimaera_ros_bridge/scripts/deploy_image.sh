#!/usr/bin/env bash
# Install a reviewed guest-root staging tree into an OFFLINE raw image.
set -euo pipefail
[[ $# == 3 ]] || { echo 'Usage: deploy_image.sh IMAGE STAGED_ROOT PARTITION' >&2; exit 2; }
image=$(realpath -e -- "$1")
staged=$(realpath -e -- "$2")
partition=$3
[[ -f "$image" && -d "$staged" && "$staged" != / && "$partition" =~ ^[1-9][0-9]*$ ]] || exit 2
if (( EUID != 0 )); then
    exec sudo bash "$(realpath -e -- "$0")" "$image" "$staged" "$partition"
fi
exec {image_lock}<"$image"
flock -n "$image_lock" || { echo 'Image locked by another deployment' >&2; exit 1; }
[[ -z "$(losetup -j "$image")" ]] || { echo 'Image already attached to a loop device' >&2; exit 1; }
mount_dir=$(mktemp -d /tmp/chimaera-ros-image.XXXXXX)
loop_device=
mounted=false
cleanup() {
    local result=$?
    trap - EXIT
    if "$mounted" && ! umount "$mount_dir"; then
        echo "Unmount failed; recover $loop_device mounted at $mount_dir" >&2
        exit 1
    fi
    if [[ -n "$loop_device" ]]; then losetup -d "$loop_device" || result=1; fi
    rmdir "$mount_dir" || result=1
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
loop_device=$(losetup --find --partscan --show "$image")
mount -o nosuid,nodev,noexec "${loop_device}p${partition}" "$mount_dir"
mounted=true
python3 - "$staged" "$mount_dir" <<'PY'
import os
from pathlib import Path
import shutil
import sys
source, target = map(Path, sys.argv[1:])
entries = sorted(source.rglob('*'))
# Check the entire tree before writing; guest symlinks never redirect host writes.
for entry in entries:
    destination = target / entry.relative_to(source)
    if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
        raise SystemExit('Unsupported staged entry: ' + str(entry))
    for parent in (destination, *destination.parents):
        if parent == target:
            break
        if parent.is_symlink():
            raise SystemExit('Refusing guest symlink: ' + str(parent))
    if entry.is_dir() and destination.exists() and not destination.is_dir():
        raise SystemExit('Destination directory conflict: ' + str(destination))
    if entry.is_file() and destination.exists() and not destination.is_file():
        raise SystemExit('Destination file conflict: ' + str(destination))
for entry in entries:
    destination = target / entry.relative_to(source)
    if entry.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, destination)
PY
sync -f "$mount_dir"
echo "Deployed $staged into $image"
