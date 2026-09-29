#!/usr/bin/env bash
# The image must be offline. The transport installer owns mount/cleanup checks.
set -euo pipefail
workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
transport=${GEM5_TRANSPORT_ROOT:-"$workspace/../../gem5-transport"}
image=${1:-"$workspace/../../gem5/resources/x86-ubuntu-22.04-ros-humble.img"}
partition=${2:-2}
parameters=${3:?Usage: deploy_guest.sh IMAGE PARTITION CONTROLLER_YAML [BRIDGE_JSON]}
config=${4:-"$workspace/src/wall_follow_bridge/config/bridge.json"}
bridge="$workspace/install/wall_follow_bridge/lib/wall_follow_bridge/guest_bridge"
robot="$workspace/install/wall_follow_robot/lib/wall_follow_robot/controller"
startup="$workspace/src/wall_follow_bridge/scripts/guest_start.sh"
for executable in "$bridge" "$robot" "$startup"; do
    [[ -x "$executable" ]] || { echo "Missing executable: $executable" >&2; exit 1; }
done
python3 -m json.tool "$config" >/dev/null
[[ -r "$parameters" ]] || { echo "Cannot read controller YAML: $parameters" >&2; exit 1; }
staging=$(mktemp -d /tmp/chimaera-wall-follow.XXXXXX)
trap 'rm -rf -- "$staging"' EXIT
cp -- "$config" "$staging/bridge.json"
cp -- "$parameters" "$staging/controller.yaml"
chmod 0755 "$staging/bridge.json" "$staging/controller.yaml"
install_guest() {
    bash "$transport/scripts/edit_disk.sh" "$image" "$1" "$partition" "$2"
}
install_guest "$bridge" /usr/local/bin/chimaera_wall_follow_bridge
install_guest "$robot" /usr/local/bin/chimaera_wall_follow_controller
install_guest "$startup" /usr/local/bin/chimaera_wall_follow_guest
install_guest "$staging/bridge.json" /usr/local/share/chimaera/wall_follow_bridge.json
install_guest "$staging/controller.yaml" /usr/local/share/chimaera/controller.yaml
