#!/usr/bin/env python3
"""Check headless rendering and ROS sensor delivery inside a worker network."""

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from ament_index_python.packages import get_package_prefix
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan

from wall_follow_benchmark.world import make_world


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/gazebo-check"))
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = args.output / "world.sdf"
    world.write_text(make_world())
    environment = dict(os.environ, IGN_PARTITION=os.environ.get("IGN_PARTITION", f"chimaera-check-{uuid.uuid4().hex}"),
                       ROS_LOCALHOST_ONLY="1", ROS_DOMAIN_ID="41")
    os.environ.update(environment)
    bridge = Path(get_package_prefix("ros_gz_bridge")) / "lib/ros_gz_bridge/parameter_bridge"
    commands = [
        ["ign", "gazebo", "-r", "-s", "--headless-rendering", "-v", "3", str(world)],
        [str(bridge),
         "/scan@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan",
         "/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock"],
    ]
    processes = []
    logs = []
    node = None
    scans = []
    clocks = []
    try:
        for name, command in zip(("gazebo", "bridge"), commands):
            log = (args.output / f"{name}.log").open("w")
            logs.append(log)
            processes.append(subprocess.Popen(command, env=environment, stdout=log,
                                              stderr=subprocess.STDOUT, start_new_session=True))
        rclpy.init()
        node = rclpy.create_node("chimaera_sensor_check")
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        node.create_subscription(LaserScan, "/scan", scans.append, qos)
        node.create_subscription(Clock, "/clock", clocks.append, qos)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(f"Simulator or adapter exited; inspect {args.output}")
            rclpy.spin_once(node, timeout_sec=0.2)
            if len(scans) >= 2 and len(clocks) >= 2:
                scan = scans[-1]
                valid = sum(math.isfinite(value) and scan.range_min <= value <= scan.range_max
                            for value in scan.ranges)
                now = clocks[-1].clock.sec * 1_000_000_000 + clocks[-1].clock.nanosec
                first = clocks[0].clock.sec * 1_000_000_000 + clocks[0].clock.nanosec
                if len(scan.ranges) == 720 and valid >= 600 and now > first:
                    print(json.dumps({"scan_samples": len(scan.ranges), "valid_ranges": valid,
                                      "scan_messages": len(scans), "clock_ns": now}))
                    return
        raise RuntimeError(f"No valid scans and advancing clock within {args.timeout}s; inspect {args.output}")
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
