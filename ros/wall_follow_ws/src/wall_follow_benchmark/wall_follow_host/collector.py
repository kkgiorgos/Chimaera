"""Passive host observer. Never publishes commands or controls either simulator."""
import json
import math
from pathlib import Path
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, qos_profile_parameter_events
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from rosgraph_msgs.msg import Clock
from rcl_interfaces.msg import ParameterEvent
import yaml

from wall_follow_benchmark.configuration import DEFAULTS, validate
from .recording import Recorder


def stamp_seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


class Collector(Node):
    def __init__(self):
        super().__init__('benchmark_collector')
        self.finished = False
        self.success = False
        self.scan = None
        self.have_pose = False
        self.sim = None
        self.last_command = None
        self.recorder = None
        self.duration = self.declare_parameter('duration', 120.).value
        self.command_timeout = self.declare_parameter('command_timeout', 1.).value
        if not math.isfinite(self.command_timeout) or self.command_timeout <= 0:
            raise ValueError('command_timeout must be positive and finite')
        directory = Path(self.declare_parameter('output_dir', 'results/run').value)
        config = Path(self.declare_parameter('parameters_file', '').value)
        configured = yaml.safe_load(config.read_text())['wall_follower']['ros__parameters']
        parameters = dict(DEFAULTS, **{k: v for k, v in configured.items() if k in DEFAULTS})
        validate(parameters)
        experiment = directory / 'experiment.json'
        provenance = json.loads(experiment.read_text()).get('source_sha256', {}) if experiment.exists() else {}
        self.create_subscription(Clock, '/clock', self.on_clock, qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/robot/scan', self.on_scan, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/ground_truth', self.on_pose, qos_profile_sensor_data)
        self.create_subscription(Twist, '/cmd_vel', self.on_command, 10)
        self.create_subscription(ParameterEvent, '/parameter_events', self.on_parameters,
                                 qos_profile_parameter_events)
        self.recorder = Recorder(directory, parameters, self.duration, provenance=provenance)

    def on_scan(self, scan):
        self.scan = scan

    def on_pose(self, pose):
        if self.sim is None or self.finished:
            return
        self.have_pose = True
        position = pose.pose.pose.position
        self.recorder.record_pose(self.sim, stamp_seconds(pose.header.stamp), position.x, position.y)

    def on_clock(self, clock):
        sim = stamp_seconds(clock.clock)
        if self.sim is not None and sim < self.sim:
            self.finish(False, 'simulation clock moved backwards')
            return
        self.sim = sim
        if self.finished or self.recorder.start is None:
            return
        if self.last_command is not None and sim - self.last_command > self.command_timeout:
            self.finish(False, 'robot command timeout')
        elif sim - self.recorder.start >= self.duration:
            self.finish(True, 'duration reached')

    def on_command(self, command):
        # Start recording once observation inputs are available. Commands go
        # directly to Gazebo regardless of this observer's readiness.
        if self.finished or self.sim is None or self.sim <= 0 or self.scan is None or not self.have_pose:
            return
        wall = time.monotonic()
        self.last_command = self.sim
        self.recorder.record_command(self.sim, wall, command.linear.x, command.angular.z,
                                     stamp_seconds(self.scan.header.stamp))

    def on_parameters(self, event):
        if event.node != '/wall_follower' or self.finished:
            return
        updates = {}
        for parameter in [*event.new_parameters, *event.changed_parameters]:
            if parameter.name in DEFAULTS:
                value = parameter.value
                updates[parameter.name] = value.integer_value if value.type == 2 else value.double_value
        if updates:
            candidate = dict(self.recorder.parameters, **updates)
            validate(candidate)
            if candidate != self.recorder.parameters:
                self.recorder.update_parameters(updates, self.sim or 0.)

    def finish(self, success=False, reason='interrupted'):
        if self.finished:
            return
        self.finished = True
        self.success = success
        if self.recorder:
            gap = (self.sim - self.last_command
                   if self.sim is not None and self.last_command is not None else None)
            self.recorder.metadata['collection_status'] = dict(
                finish_sim_time=self.sim, last_command_sim_time=self.last_command,
                command_gap_sim_seconds=gap,
                command_timeout_sim_seconds=self.command_timeout)
            self.recorder.finish(success, reason)
            self.success = self.recorder.metadata['completed']
            details = [f'{self.recorder.count} command receipts']
            if self.sim is not None:
                details.append(f'simulation time {self.sim:.3f}s')
            if gap is not None:
                details.append(f'last command {self.last_command:.3f}s, gap {gap:.3f}s '
                               f'(timeout {self.command_timeout:.3f}s)')
            message = f'Collection {"completed" if self.success else "failed"}: {reason}; ' + '; '.join(details)
            (self.get_logger().info if self.success else self.get_logger().error)(message)


def main():
    rclpy.init()
    node = None
    code = 1
    try:
        node = Collector()
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=.1)
        code = 0 if node.success else 1
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        if node:
            node.get_logger().error(str(exc))
        else:
            print(str(exc), flush=True)
    finally:
        if node:
            node.finish()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return code
