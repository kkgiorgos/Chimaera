from pathlib import Path
import time
import numpy as np
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Vector3Stamped
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from builtin_interfaces.msg import Duration

from .model import Arm
from .motion import Quintic
from .vision import GRAVITY
from .runtime import Recorder, run, seconds


class Intercept(Node):
    def __init__(self):
        super().__init__('ball_intercept')
        self.declare_parameter('robot_file', '')
        self.declare_parameter('initial_pose', [0., -0.785398, 0., -2.356194, 0., 1.570796, 0.785398])
        self.declare_parameter('output', '')
        self.arm = Arm(Path(self.get_parameter('robot_file').value).read_text())
        pose = self.arm.pose(self.get_parameter('initial_pose').value)
        self.rotation, self.height = pose.M, pose.p.z()
        self.q = self.dq = self.ball = None
        self.radius = None
        self.joint_time = -1
        self.trajectory = None
        self.last_plan, self.hold_until = -1., -1.
        self.record = Recorder(self.get_parameter('output').value, 'interception')
        self.commands = self.create_publisher(JointTrajectory, '/robot/joint_trajectory', 1)
        self.create_subscription(JointState, '/robot/joint_states', self.joints, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/robot/ball_state', self.observe, qos_profile_sensor_data)
        self.create_subscription(Vector3Stamped, '/robot/ball_geometry', self.geometry, qos_profile_sensor_data)
        self.create_timer(1 / 30, self.plan)

    def joints(self, msg):
        try:
            indices = [msg.name.index(name) for name in self.arm.names]
            self.q = np.array([msg.position[i] for i in indices])
            self.dq = np.array([msg.velocity[i] for i in indices])
            self.joint_time = seconds(msg.header.stamp)
        except (ValueError, IndexError):
            return

    def observe(self, msg):
        if msg.header.frame_id != 'world':
            return
        p, v = msg.pose.pose.position, msg.twist.twist.linear
        self.ball = (seconds(msg.header.stamp), np.array([p.x, p.y, p.z]), np.array([v.x, v.y, v.z]))

    def geometry(self, msg):
        if 0.005 < msg.vector.x < 0.15:
            self.radius = msg.vector.x if self.radius is None else 0.2 * msg.vector.x + 0.8 * self.radius

    def plan(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        if self.q is None or self.ball is None or self.radius is None or now < self.hold_until:
            return
        capture, position, velocity = self.ball
        if now - capture > 0.1 or now - self.joint_time > 0.05 or now - self.last_plan < 0.06:
            return
        age = now - capture
        position = position + velocity * age + 0.5 * GRAVITY * age ** 2
        velocity = velocity + GRAVITY * age
        cup = self.arm.pose(self.q).p
        if (position[2] < cup.z() + 0.025 and
                np.linalg.norm(position[:2] - [cup.x(), cup.y()]) < 0.13):
            # Freeze based on visual entry, never on the privileged scorer.
            self.hold_until = now + 2.
            return
        start = time.perf_counter()
        current_q, current_dq, current_ddq = self.q, self.dq, np.zeros(7)
        if self.trajectory:
            current_q, current_dq, current_ddq = self.trajectory.sample(now)
        # Descending intersections with several horizontal planes. The ball
        # center arrives slightly above the physical rim to clear its radius.
        candidates = []
        for height in [self.height, self.height + 0.10, self.height - 0.10,
                       self.height - 0.20, self.height + 0.20]:
            discriminant = velocity[2] ** 2 + 2 * 9.81 * (position[2] - height - self.radius - 0.005)
            if discriminant < 0:
                continue
            dt = (velocity[2] + np.sqrt(discriminant)) / 9.81
            if not 0.07 < dt < 1.2:
                continue
            point = position + velocity * dt + 0.5 * GRAVITY * dt ** 2
            point[2] = height
            if not (0.12 < point[0] < 0.78 and abs(point[1]) < 0.60 and 0.35 < height < 1.05):
                continue
            target = self.arm.inverse(point, self.rotation, self.q)
            if target is None:
                continue
            trajectory = Quintic(now, dt, current_q, current_dq, current_ddq, target)
            if trajectory.feasible(self.arm):
                candidates.append((np.linalg.norm(target - current_q), point, target, trajectory))
        if not candidates:
            self.record.write(time=now, capture_time=capture, status='no_feasible_intercept',
                              planning_wall_seconds=time.perf_counter() - start)
            return
        _, point, target, trajectory = min(candidates, key=lambda x: x[0])
        self.trajectory, self.last_plan = trajectory, now
        msg = JointTrajectory()
        msg.header.stamp = self.get_clock().now().to_msg()
        # Preserve the timestamp used for planning rather than resetting timing
        # after an expensive solve. A delayed command is evaluated at its age.
        msg.header.stamp.sec = int(now)
        msg.header.stamp.nanosec = int((now - int(now)) * 1e9)
        msg.joint_names = self.arm.names
        for dt, q, dq, ddq in [(0., current_q, current_dq, current_ddq),
                               (trajectory.duration, target, np.zeros(7), np.zeros(7))]:
            waypoint = JointTrajectoryPoint()
            waypoint.positions, waypoint.velocities, waypoint.accelerations = q.tolist(), dq.tolist(), ddq.tolist()
            waypoint.time_from_start = Duration(sec=int(dt), nanosec=int((dt - int(dt)) * 1e9))
            msg.points.append(waypoint)
        self.commands.publish(msg)
        self.record.write(time=now, capture_time=capture, status='planned', target=point.tolist(),
                          target_joints=target.tolist(), intercept_time=now + trajectory.duration,
                          planning_wall_seconds=time.perf_counter() - start)


def main():
    run(Intercept)
