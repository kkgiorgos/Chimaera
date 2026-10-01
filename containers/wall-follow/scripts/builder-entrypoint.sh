#!/usr/bin/env bash
set -e
source "/opt/ros/${ROS_DISTRO}/setup.bash"
if [[ -f /opt/chimaera/ros/local_setup.bash ]]; then
    source /opt/chimaera/ros/local_setup.bash
fi
exec "$@"
