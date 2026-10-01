#!/usr/bin/env bash
set -eo pipefail
jobs=$1
source "/opt/ros/${ROS_DISTRO}/setup.bash"
export MAKEFLAGS="-j${jobs}"
export CMAKE_BUILD_PARALLEL_LEVEL="$jobs"
cd /src/ros/wall_follow_ws
colcon build --base-paths src /src/ros/chimaera_ros_ws/src --merge-install --install-base /opt/chimaera/ros --executor sequential \
    --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_GAZEBO_BRIDGE=ON -DGEM5_ROOT=/src/gem5 \
    -DGEM5_M5_LIBRARY=/opt/chimaera/gem5/util/m5/build/x86/out/libm5.a
