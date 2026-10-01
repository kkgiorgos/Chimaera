#!/usr/bin/env bash
set -euo pipefail
workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
manifest=${3:-"$workspace/src/talker_listener_bridge/config/session.json"}
image=${1:-"$workspace/../../gem5/resources/x86-ubuntu-22.04-ros-humble.img"}
partition=${2:-2}
staged=$(mktemp -d /tmp/chimaera-ros-stage.XXXXXX)
trap 'rm -rf -- "$staged"' EXIT
ros2 run chimaera_ros_bridge chimaera_ros stage "$manifest" --output "$staged/root"
ros2 run chimaera_ros_bridge chimaera_ros deploy "$manifest" --output "$staged/root" \
    --image "$image" --partition "$partition"
