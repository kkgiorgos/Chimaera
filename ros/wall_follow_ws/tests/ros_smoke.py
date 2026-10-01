#!/usr/bin/env python3
"""Optional ROS integration check: separate processes, synthetic sensors, no Gazebo needed.
Run after sourcing the workspace: python3 tests/ros_smoke.py
"""
import csv
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import rclpy
from rclpy.parameter import Parameter
from rcl_interfaces.srv import SetParameters
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from rosgraph_msgs.msg import Clock
from ament_index_python.packages import get_package_prefix


def run_clockless():
    """Check robot scheduling and scan timeout without publishing a ROS clock."""
    with tempfile.TemporaryDirectory(prefix='wall-follow-clockless-') as directory:
        root = Path(directory)
        executable = Path(get_package_prefix('wall_follow_robot'))/'lib/wall_follow_robot/controller'
        with (root/'robot.log').open('w') as log:
            robot = subprocess.Popen([str(executable), '--ros-args',
                '-p', 'use_sim_time:=false', '-p', 'scan_timeout:=0.1',
                '-r', 'scan:=/clockless/scan', '-r', 'cmd_vel:=/clockless/cmd_vel'],
                stdout=log, stderr=subprocess.STDOUT)
            node = rclpy.create_node('clockless_smoke_driver')
            scans = node.create_publisher(LaserScan, '/clockless/scan', 10)
            commands = []
            node.create_subscription(Twist, '/clockless/cmd_vel', lambda m: commands.append(m), 10)
            def wait_for(predicate, timeout=5):
                deadline = time.monotonic()+timeout
                while not predicate():
                    assert robot.poll() is None, 'Robot exited during clockless test'
                    assert time.monotonic()<deadline, 'Clockless robot check timed out'
                    rclpy.spin_once(node, timeout_sec=.01)
            try:
                wait_for(lambda: scans.get_subscription_count()==1 and bool(commands))
                assert commands[-1].linear.x == 0.0, 'Missing scan did not stop translation'
                wait_for(lambda: ('wall_follower', '/') in node.get_node_names_and_namespaces())
                subscriptions = dict(node.get_subscriber_names_and_types_by_node('wall_follower', '/'))
                assert '/clock' not in subscriptions, 'Robot subscribed to ROS clock'
                scan = LaserScan()
                scan.angle_min = -math.pi
                scan.angle_increment = 2*math.pi/719
                scan.range_min, scan.range_max = .05, 20.
                scan.ranges = [.8/-math.sin(scan.angle_min+i*scan.angle_increment)
                    if math.sin(scan.angle_min+i*scan.angle_increment)<-1e-6 else math.inf
                    for i in range(720)]
                # Both stamps deliberately differ from the machine's local epoch.
                for seconds in (0, 2_000_000_000):
                    scan.header.stamp.sec = seconds
                    commands.clear()
                    scans.publish(scan)
                    wait_for(lambda: any(command.linear.x>0 for command in commands))
                    moving_count = len(commands)
                    wait_for(lambda: len(commands)>moving_count and commands[-1].linear.x==0.0)
                    assert commands[-1].angular.z == 0.0, 'Stale scan did not stop rotation'
                print('clockless: steady control timer, foreign scan stamps and receipt timeout passed')
            except BaseException:
                print((root/'robot.log').read_text())
                raise
            finally:
                node.destroy_node()
                if robot.poll() is None:
                    robot.send_signal(signal.SIGINT)
                try:
                    robot.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    robot.kill()
                    robot.wait()


def run(mode):
    with tempfile.TemporaryDirectory(prefix='wall-follow-split-') as temporary:
        root = Path(temporary)
        config = root/'controller.yaml'
        config.write_text(json.dumps({'wall_follower': {'ros__parameters': {'control_hz': 20.}}}))
        robot_cwd = root/'robot'
        robot_cwd.mkdir()
        robot_executable = Path(get_package_prefix('wall_follow_robot'))/'lib/wall_follow_robot/controller'
        collector_executable = Path(get_package_prefix('wall_follow_benchmark'))/'lib/wall_follow_benchmark/collector'
        with (root/'robot.log').open('w') as robot_log, (root/'host.log').open('w') as host_log:
            robot = subprocess.Popen([str(robot_executable), '--ros-args', '--params-file', str(config),
                '-p', 'use_sim_time:=false', '-r', 'scan:=/robot/scan', '-r', 'cmd_vel:=/robot/cmd_vel'],
                cwd=robot_cwd, stdout=robot_log, stderr=subprocess.STDOUT)
            host = subprocess.Popen([sys.executable, str(collector_executable), '--ros-args',
                '-p', f'parameters_file:={config}', '-p', f'output_dir:={root / "result"}',
                '-p', 'duration:=1.0', '-p', 'command_timeout:=0.3'],
                stdout=host_log, stderr=subprocess.STDOUT)
            node = rclpy.create_node('split_smoke_driver')
            clock = node.create_publisher(Clock, '/clock', 10)
            scan_pub = node.create_publisher(LaserScan, '/robot/scan', 10)
            pose_pub = node.create_publisher(Odometry, '/ground_truth', 10)
            forwarded = []
            node.create_subscription(Twist, '/cmd_vel', lambda m: forwarded.append(m.linear.x), 10)
            parameters = node.create_client(SetParameters, '/wall_follower/set_parameters')
            try:
                deadline = time.monotonic()+10
                while clock.get_subscription_count()<1 or scan_pub.get_subscription_count()<2:
                    assert time.monotonic()<deadline, 'ROS discovery timed out'
                    assert robot.poll() is None and host.poll() is None
                    rclpy.spin_once(node, timeout_sec=.05)
                changed = None
                start_wall = time.monotonic()
                step = 0
                while host.poll() is None and time.monotonic()-start_wall<8:
                    step += 1
                    sim = step*.02
                    if mode == 'rewind' and step>30:
                        sim = .1
                    stamp = Clock()
                    stamp.clock.sec = int(sim)
                    stamp.clock.nanosec = int(round((sim-int(sim))*1e9))
                    clock.publish(stamp)
                    scan = LaserScan()
                    scan.header.stamp = stamp.clock
                    scan.angle_min = -math.pi
                    scan.angle_increment = 2*math.pi/719
                    scan.range_min, scan.range_max = .05, 20.
                    scan.ranges = [.8/-math.sin(scan.angle_min+i*scan.angle_increment)
                        if math.sin(scan.angle_min+i*scan.angle_increment)<-1e-6 else math.inf
                        for i in range(720)]
                    scan_pub.publish(scan)
                    pose = Odometry()
                    pose.header.stamp = stamp.clock
                    pose.pose.pose.position.y = -3.2
                    pose_pub.publish(pose)
                    if step == 15 and mode == 'complete':
                        request = SetParameters.Request()
                        request.parameters = [Parameter('target_distance', value=.9).to_parameter_msg()]
                        changed = parameters.call_async(request)
                    if step == 25 and mode == 'timeout':
                        robot.send_signal(signal.SIGINT)
                    until = time.monotonic()+.025
                    while time.monotonic()<until:
                        rclpy.spin_once(node, timeout_sec=.005)
                assert host.wait(timeout=3) == (0 if mode == 'complete' else 1)
                metadata = json.loads((root/'result/metadata.json').read_text())
                rows = list(csv.DictReader((root/'result/samples.csv').open()))
                assert metadata['completed'] == (mode == 'complete')
                assert len(rows)>2 and any(value>0 for value in forwarded)
                assert forwarded[-1] == 0.
                assert not list(robot_cwd.iterdir()), 'Robot process wrote experiment files'
                assert 'compute_ms' not in rows[0]
                assert not any(key.startswith('reference_') for key in rows[0])
                assert all(math.isfinite(float(row['host_scan_age'])) for row in rows)
                assert not {'cpu_seconds', 'rss_kib'} & rows[0].keys()
                assert 'robot_pid' not in metadata
                if mode == 'complete':
                    assert changed.done() and changed.result().results[0].successful
                    assert metadata['final_parameters']['target_distance'] == .9
                    subscriptions = node.get_subscriber_names_and_types_by_node('wall_follower', '/')
                    assert '/clock' not in dict(subscriptions)
                    assert '/ground_truth' not in dict(subscriptions)
                    assert '/robot/scan' in dict(subscriptions)
                    assert metadata['parameter_events']
                print(f'{mode}: {len(rows)} samples; {metadata["finish_reason"]}')
            except BaseException:
                print((root/'host.log').read_text())
                print((root/'robot.log').read_text())
                raise
            finally:
                node.destroy_node()
                for process in (host, robot):
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    rclpy.init()
    try:
        run_clockless()
        for mode in ('complete', 'timeout', 'rewind'):
            run(mode)
    finally:
        rclpy.shutdown()
