import csv
import hashlib
import os
import json
import math
import platform
from pathlib import Path
import resource
import time

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import SetParametersResult
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from .core import DEFAULTS, validate, command
from .world import validate_arena, wall_distance


class Controller(Node):
    def __init__(self):
        super().__init__('wall_follower')
        for key, value in DEFAULTS.items():
            self.declare_parameter(key, value)
        self.declare_parameter('duration', 120.)
        self.declare_parameter('arena_width', 12.)
        self.declare_parameter('arena_height', 8.)
        self.arena_width = self.get_parameter('arena_width').value
        self.arena_height = self.get_parameter('arena_height').value
        validate_arena(self.arena_width, self.arena_height)
        self.declare_parameter('output_dir', 'results/run')
        self.p = {k: self.get_parameter(k).value for k in DEFAULTS}
        validate(self.p)
        self.duration = self.get_parameter('duration').value
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError('duration must be positive')
        self.directory = Path(self.get_parameter('output_dir').value).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = (self.directory/'samples.csv').open('x', buffering=1)
        self.writer = csv.DictWriter(self.file, fieldnames=[
            'sim_time','elapsed','wall_elapsed','dt_sim','dt_wall','control_hz','compute_ms','scan_age',
            'state','estimated_distance','heading','gt_distance','gt_error','gt_age',
            'x','y','path_m','linear_cmd','angular_cmd','front_clearance','cpu_seconds','rss_kib'])
        self.writer.writeheader()
        self.meta = dict(parameters=self.p.copy(), duration=self.duration,
                         arena=dict(arena_width=self.arena_width, arena_height=self.arena_height),
                         platform=platform.platform(), machine=platform.machine(),
                         python=platform.python_version(), processor=platform.processor(),
                         ros_distro=os.environ.get('ROS_DISTRO', ''),
                         source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in Path(__file__).parent.glob('*.py')},
                         parameter_events=[], completed=False)
        self.save_metadata()
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.create_subscription(LaserScan, '/scan', self.on_scan, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/ground_truth', self.on_pose, qos_profile_sensor_data)
        self.scan = None
        self.pose = None
        self.path = 0.
        self.start = self.last_sim = self.last_wall = None
        self.finished = False
        self.timer = self.create_timer(1/self.p['control_hz'], self.tick)
        self.add_on_set_parameters_callback(self.parameters_changed)

    def save_metadata(self):
        (self.directory/'metadata.json').write_text(json.dumps(self.meta, indent=2))

    def parameters_changed(self, parameters):
        candidate = self.p.copy()
        for p in parameters:
            if p.name not in DEFAULTS:
                return SetParametersResult(successful=False, reason='Only controller parameters are mutable during a run')
            candidate[p.name] = p.value
        try:
            validate(candidate)
        except ValueError as e:
            return SetParametersResult(successful=False, reason=str(e))
        if candidate['control_hz'] != self.p['control_hz']:
            self.timer.timer_period_ns = int(1e9/candidate['control_hz'])
            self.timer.reset()
        self.p = candidate
        self.meta['parameter_events'].append(dict(sim_time=self.now(), parameters=candidate.copy()))
        self.save_metadata()
        return SetParametersResult(successful=True)

    def now(self):
        return self.get_clock().now().nanoseconds/1e9

    def on_scan(self, msg):
        self.scan = msg

    def on_pose(self, msg):
        pt = msg.pose.pose.position
        if self.pose is not None:
            self.path += math.hypot(pt.x-self.pose[0], pt.y-self.pose[1])
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec/1e9
        self.pose = (pt.x, pt.y, stamp)

    def tick(self):
        begin = time.perf_counter()
        now = self.now()
        if self.start is None:
            self.start, self.wall_start, self.cpu_start = now, begin, time.process_time()
        elapsed = now-self.start
        if elapsed >= self.duration:
            self.finish(True)
            return
        dt_sim = now-self.last_sim if self.last_sim is not None else float('nan')
        dt_wall = begin-self.last_wall if self.last_wall is not None else float('nan')
        self.last_sim, self.last_wall = now, begin
        age = float('nan')
        v, w, distance, heading, state, front = 0., 0., float('nan'), float('nan'), 'waiting_scan', float('nan')
        if self.scan is not None:
            scan = self.scan
            age = now-(scan.header.stamp.sec+scan.header.stamp.nanosec/1e9)
            if 0 <= age <= self.p['scan_timeout']:
                v,w,distance,heading,state,front = command(scan.ranges, scan.angle_min, scan.angle_increment,
                                                         scan.range_min, scan.range_max, self.p)
            else:
                state = 'stale_scan'
        msg = Twist()
        msg.linear.x, msg.angular.z = float(v), float(w)
        self.pub.publish(msg)
        compute = (time.perf_counter()-begin)*1000
        x,y,gt,gt_age = [float('nan')]*4
        if self.pose is not None:
            x,y,stamp = self.pose
            gt_age = now-stamp
            if 0 <= gt_age <= .2:
                gt = wall_distance(x, y, self.arena_width, self.arena_height)
        self.writer.writerow(dict(sim_time=now, elapsed=elapsed, wall_elapsed=begin-self.wall_start,
            dt_sim=dt_sim, dt_wall=dt_wall, control_hz=self.p['control_hz'], compute_ms=compute, scan_age=age, state=state,
            estimated_distance=distance, heading=heading, gt_distance=gt,
            gt_error=gt-self.p['target_distance'], gt_age=gt_age, x=x,y=y,path_m=self.path,
            linear_cmd=v,angular_cmd=w,front_clearance=front,
            cpu_seconds=time.process_time()-self.cpu_start,
            rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))

    def finish(self, completed=False):
        if self.finished:
            return
        self.pub.publish(Twist())
        self.timer.cancel()
        self.finished = True
        self.file.close()
        self.meta['completed'] = completed
        self.meta['final_parameters'] = self.p.copy()
        self.save_metadata()
        self.get_logger().info(f'Run saved to {self.directory} (completed={completed})')


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = Controller()
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=.5)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            if rclpy.ok():
                node.finish()
            else:
                node.file.close()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
