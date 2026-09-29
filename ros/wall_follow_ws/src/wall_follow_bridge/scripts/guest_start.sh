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
/usr/local/bin/chimaera_wall_follow_controller --ros-args \
    --params-file "${CHIMAERA_ROBOT_CONFIG:-/usr/local/share/chimaera/controller.yaml}" \
    -p use_sim_time:=true -r scan:=/robot/scan -r cmd_vel:=/robot/cmd_vel &
pids+=("$!")
/usr/local/bin/chimaera_wall_follow_bridge --ros-args -p \
    "config_file:=${CHIMAERA_BRIDGE_CONFIG:-/usr/local/share/chimaera/wall_follow_bridge.json}" &
pids+=("$!")
# If any component exits, terminate the remaining guest application processes.
wait -n "${pids[@]}"
