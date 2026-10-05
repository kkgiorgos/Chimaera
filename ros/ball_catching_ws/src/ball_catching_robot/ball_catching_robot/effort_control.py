from pathlib import Path
import numpy as np
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory
from std_msgs.msg import Bool

from .model import Arm
from .motion import Quintic
from .runtime import Recorder, run, seconds


class EffortControl(Node):
    def __init__(self):
        super().__init__('ball_effort_control')
        self.declare_parameter('robot_file', '')
        self.declare_parameter('initial_pose', [0., -0.785398, 0., -2.356194, 0., 1.570796, 0.785398])
        self.declare_parameter('output', '')
        self.declare_parameter('control_hz', 250.)
        self.arm = Arm(Path(self.get_parameter('robot_file').value).read_text())
        self.home = np.array(self.get_parameter('initial_pose').value)
        self.q = self.dq = self.trajectory = None
        self.joint_time = -1
        self.record = Recorder(self.get_parameter('output').value, 'control')
        self.last_log = -1
        self.kp = np.full(7, 144.)
        self.kd = np.full(7, 24.)
        self.commands = self.create_publisher(JointState, '/robot/effort_command', qos_profile_sensor_data)
        self.ready = self.create_publisher(Bool, '/robot/ready', 1)
        self.create_subscription(JointState, '/robot/joint_states', self.joints, qos_profile_sensor_data)
        self.create_subscription(JointTrajectory, '/robot/joint_trajectory', self.target, 1)
        self.create_timer(1 / self.get_parameter('control_hz').value, self.control)

    def joints(self, msg):
        try:
            indices = [msg.name.index(name) for name in self.arm.names]
            self.q = np.array([msg.position[i] for i in indices])
            self.dq = np.array([msg.velocity[i] for i in indices])
            self.joint_time = seconds(msg.header.stamp)
        except (ValueError, IndexError):
            return

    def target(self, msg):
        if msg.joint_names != self.arm.names or len(msg.points) != 2:
            return
        first, last = msg.points
        if any(len(x) != 7 for x in [first.positions, first.velocities, first.accelerations, last.positions]):
            return
        duration = seconds(last.time_from_start)
        if duration <= 0:
            return
        trajectory = Quintic(seconds(msg.header.stamp), duration, np.array(first.positions),
                              np.array(first.velocities), np.array(first.accelerations), last.positions)
        if not np.isfinite(trajectory.coefficients).all() or not trajectory.feasible(self.arm):
            return
        self.trajectory = trajectory

    def control(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        if self.q is None or not 0 <= now - self.joint_time < 0.05:
            return
        desired, velocity, acceleration = self.home, np.zeros(7), np.zeros(7)
        if self.trajectory:
            desired, velocity, acceleration = self.trajectory.sample(now)
        # Computed-torque feedback scales gains by physical inertia, avoiding
        # excessive damping/oscillation on the lightweight wrist joints.
        correction = acceleration + self.kp * (desired - self.q) + self.kd * (velocity - self.dq)
        effort = self.arm.forces(self.q, self.dq) + self.arm.mass(self.q) @ correction
        # Additional braking near limits / above nominal joint speeds.
        effort -= 20 * np.sign(self.dq) * np.maximum(np.abs(self.dq) - self.arm.velocity, 0)
        effort = np.clip(effort, -self.arm.effort, self.arm.effort)
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name, msg.effort = self.arm.names, effort.tolist()
        self.commands.publish(msg)
        if now - self.last_log > 0.04:
            self.last_log = now
            self.ready.publish(Bool(data=bool(self.trajectory is None and
                np.max(np.abs(self.q - self.home)) < 0.025 and np.max(np.abs(self.dq)) < 0.10)))
            self.record.write(time=now, feedback_time=self.joint_time, joints=self.q.tolist(),
                              velocity=self.dq.tolist(), desired=desired.tolist(), effort=effort.tolist())


def main():
    run(EffortControl)
