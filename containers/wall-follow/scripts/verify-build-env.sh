#!/usr/bin/env bash
set -euo pipefail
python3 -c 'import rclpy, yaml; print("ROS Python and PyYAML imports passed")'
pkg-config --modversion ignition-transport11 ignition-msgs8 json-c
ros2 pkg prefix ros_gz_sim
ros2 pkg prefix ros_gz_bridge
