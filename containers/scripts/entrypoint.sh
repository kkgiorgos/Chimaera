#!/usr/bin/env bash
set -eo pipefail
export HOME=/tmp/chimaera-home
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_RUNTIME_DIR="$HOME/runtime"
export ROS_LOG_DIR="$HOME/ros-log"
mkdir -p "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR" "$ROS_LOG_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
source "/opt/ros/$ROS_DISTRO/setup.bash"
source /opt/chimaera/ros/local_setup.bash
# Optional colon-separated setup files from mounted benchmark overlays.
IFS=: read -ra chimaera_setups <<< "${CHIMAERA_SETUP:-}"
for chimaera_setup in "${chimaera_setups[@]}"; do
    source "$chimaera_setup"
done
exec "$@"
