#!/usr/bin/env bash
# Run only with gem5/QEMU stopped. Reuse the transport's guarded image installer.
set -euo pipefail
workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
transport=${GEM5_TRANSPORT_ROOT:-"$workspace/../../gem5-transport"}
image=${1:-"$workspace/../../gem5/resources/x86-ubuntu-22.04-ros-humble.img"}
partition=${2:-2}
binary="$workspace/install/talker_listener_bridge/lib/talker_listener_bridge/guest_bridge"
config=${3:-"$workspace/src/talker_listener_bridge/config/bridge.json"}
python3 -m json.tool "$config" >/dev/null
# The existing guarded installer requires executable input, including data files.
# Stage a copy; do not change the source JSON permissions.
staged_config=$(mktemp /tmp/chimaera-bridge-config.XXXXXX)
trap 'rm -f -- "$staged_config"' EXIT
cp -- "$config" "$staged_config"
chmod 0755 "$staged_config"
startup="$workspace/src/talker_listener_bridge/scripts/guest_start.sh"
[[ -x "$binary" ]] || { echo "Build the guest bridge before deploying." >&2; exit 1; }
bash "$transport/scripts/edit_disk.sh" "$image" "$binary" "$partition" /usr/local/bin/chimaera_guest_bridge
bash "$transport/scripts/edit_disk.sh" "$image" "$startup" "$partition" /usr/local/bin/chimaera_talker_listener_guest
bash "$transport/scripts/edit_disk.sh" "$image" "$staged_config" "$partition" /usr/local/share/chimaera/bridge.json
