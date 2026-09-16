#!/usr/bin/env bash
# Deploy into an offline raw disk image; always release resources on exit.
set -euo pipefail

if [[ $# != 4 ]]; then
    echo "Usage: $0 IMAGE EXECUTABLE PARTITION /GUEST/DESTINATION" >&2
    exit 2
fi

image=$(realpath -e -- "$1")
binary=$(realpath -e -- "$2")
partition=$3
guest_path=$4
[[ -f "$image" && -f "$binary" && -x "$binary" ]] || {
    echo "Expected a disk image and an executable regular file." >&2
    exit 2
}
[[ "$partition" =~ ^[1-9][0-9]*$ && "$guest_path" == /* && "$guest_path" != */ ]] || {
    echo "Expected a positive partition number and an absolute guest file path." >&2
    exit 2
}

if (( EUID != 0 )); then
    exec sudo bash "$(realpath -e -- "$0")" "$image" "$binary" "$partition" "$guest_path"
fi

# Serialize deployments through this script. Do not edit a running VM's image.
exec {image_lock}<"$image"
flock -n "$image_lock" || { echo "Image is locked by another process." >&2; exit 1; }
if [[ -n "$(losetup -j "$image")" ]]; then
    echo "Image already has a loop device attached; refusing to mount it again." >&2
    exit 1
fi

loop_device=
mount_dir=$(mktemp -d /tmp/chimaera-image.XXXXXX)
mounted=false
cleanup() {
    local result=$?
    trap - EXIT
    if "$mounted"; then
        if ! umount "$mount_dir"; then
            echo "Unmount failed; leaving $loop_device attached at $mount_dir for recovery." >&2
            exit 1
        fi
    fi
    if [[ -n "$loop_device" ]]; then
        losetup -d "$loop_device" || result=1
    fi
    rmdir "$mount_dir" || result=1
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

loop_device=$(losetup --find --partscan --show "$image")
mount -o nosuid,nodev,noexec "${loop_device}p${partition}" "$mount_dir"
mounted=true

# Resolve the parent first so guest symlinks cannot redirect the host copy
# outside the mounted filesystem. Never follow a destination-file symlink.
destination=$(realpath -m -- "$mount_dir$guest_path")
if [[ "$destination" != "$mount_dir/"* || -L "$mount_dir$guest_path" ]]; then
    echo "Guest destination escapes the image or is a symlink." >&2
    exit 1
fi
install -D -m 0755 -- "$binary" "$destination"
sync -f "$mount_dir"
echo "Deployed $binary to $image:$guest_path"
