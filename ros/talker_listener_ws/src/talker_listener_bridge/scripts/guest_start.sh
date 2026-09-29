#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
export ROS_LOCALHOST_ONLY=1
export ROS_DOMAIN_ID=42
export ROS_LOG_DIR=/tmp/chimaera-guest-logs
pids=()
cleanup() {
    trap - EXIT INT TERM
    if ((${#pids[@]})); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ros2 run demo_nodes_cpp talker --ros-args -r __node:=guest_talker -r chatter:=/guest/chatter &
pids+=("$!")
ros2 run demo_nodes_cpp listener --ros-args -r __node:=guest_listener -r chatter:=/host/chatter &
pids+=("$!")
/usr/local/bin/chimaera_guest_bridge --ros-args -p \
    "config_file:=${CHIMAERA_BRIDGE_CONFIG:-/usr/local/share/chimaera/bridge.json}" &
pids+=("$!")
# If any component exits, terminate the remaining guest application processes.
wait -n "${pids[@]}"
