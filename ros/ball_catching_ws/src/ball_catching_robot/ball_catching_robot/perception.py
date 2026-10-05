import time
import cv2
import numpy as np
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Vector3Stamped
from std_msgs.msg import Bool

from .vision import Stereo, BallFilter
from .runtime import Recorder, run, seconds


class Perception(Node):
    def __init__(self):
        super().__init__('ball_perception')
        cv2.setNumThreads(1)
        self.declare_parameter('focal', 400.)
        self.declare_parameter('cx', 320.)
        self.declare_parameter('cy', 240.)
        self.declare_parameter('baseline', 0.3)
        self.declare_parameter('camera_origin', [-0.65, 0.15, 1.0])
        self.declare_parameter('output', '')
        self.stereo = Stereo(**{name: self.get_parameter(name).value for name in
                                ('focal', 'cx', 'cy', 'baseline', 'camera_origin')})
        self.filter = BallFilter()
        self.buffers = [{}, {}]
        self.last_pair = -1
        self.record = Recorder(self.get_parameter('output').value, 'perception')
        self.state = self.create_publisher(Odometry, '/robot/ball_state', qos_profile_sensor_data)
        self.geometry = self.create_publisher(Vector3Stamped, '/robot/ball_geometry', qos_profile_sensor_data)
        self.ready = self.create_publisher(Bool, '/robot/perception_ready', 1)
        self.last_images = -1.
        self.create_timer(0.1, self.readiness)
        self.subscriptions_ = [self.create_subscription(Image, f'/stereo/{side}/image_raw',
            lambda msg, index=index: self.image(msg, index), qos_profile_sensor_data)
            for index, side in enumerate(('left', 'right'))]

    def readiness(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        self.ready.publish(Bool(data=0 <= now - self.last_images < 0.2))

    def image(self, message, side):
        stamp = message.header.stamp
        key = stamp.sec * 1000000000 + stamp.nanosec
        if key <= self.last_pair:
            return
        self.buffers[side][key] = message
        # Exact timestamp matching; bounded queues drop unmatched/stale frames.
        for buffer in self.buffers:
            for old in sorted(buffer)[:-3]:
                del buffer[old]
        if key not in self.buffers[1 - side]:
            return
        self.last_pair = key
        pair = [buffer.pop(key) for buffer in self.buffers]
        self.last_images = self.get_clock().now().nanoseconds * 1e-9
        start = time.perf_counter()
        images = []
        for msg in pair:
            if msg.encoding not in ('rgb8', 'bgr8') or msg.step < msg.width * 3:
                self.get_logger().error('Expected RGB8/BGR8 stereo images')
                return
            image = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
            image = image[:, :msg.width * 3].reshape(msg.height, msg.width, 3)
            images.append(image if msg.encoding == 'rgb8' else image[:, :, ::-1])
        timestamp = seconds(stamp)
        predicted = self.filter.predict(timestamp)
        detection = self.stereo.detect(*images, predicted=predicted)
        if detection is None:
            return
        point, radius = detection
        geometry = Vector3Stamped()
        geometry.header.stamp, geometry.header.frame_id = stamp, 'ball'
        geometry.vector.x = float(radius)
        self.geometry.publish(geometry)
        estimate = self.filter.observe(point, timestamp)
        self.record.write(capture_time=timestamp, receive_time=self.last_images,
                          processing_wall_seconds=time.perf_counter() - start,
                          observation=point.tolist(), radius=radius,
                          state=None if estimate is None else estimate.tolist())
        if estimate is None:
            return
        state = Odometry()
        state.header.stamp = stamp
        state.header.frame_id = 'world'
        state.child_frame_id = 'ball'
        state.pose.pose.position.x, state.pose.pose.position.y, state.pose.pose.position.z = estimate[:3]
        state.pose.pose.orientation.w = 1.
        state.twist.twist.linear.x, state.twist.twist.linear.y, state.twist.twist.linear.z = estimate[3:]
        covariance = self.filter.covariance
        for i in range(3):
            for j in range(3):
                state.pose.covariance[i * 6 + j] = covariance[i, j]
                state.twist.covariance[i * 6 + j] = covariance[i + 3, j + 3]
        self.state.publish(state)


def main():
    run(Perception)
