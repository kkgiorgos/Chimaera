#!/usr/bin/env python3
"""
Robot distance controller node.
Moves the robot forward for a given distance at a given velocity and records the time taken.
Uses ROS simulation time from /clock topic.
"""

from math import sqrt

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


class DistanceController(Node):
    """Control robot movement to achieve a target distance."""

    def __init__(self):
        super().__init__('distance_controller')
        
        # Declare parameters
        self.declare_parameter('target_distance', 1.0)  # meters
        self.declare_parameter('velocity', 0.5)  # m/s
        
        self.target_distance = self.get_parameter('target_distance').value
        self.velocity = self.get_parameter('velocity').value
        
        self.get_logger().info(
            f'Target distance: {self.target_distance}m, Velocity: {self.velocity}m/s'
        )
        
        # Publishers and subscribers
        self.cmd_vel_pub = self.create_publisher(
            Twist, '/diff_drive/cmd_vel', 10
        )
        self.odom_sub = self.create_subscription(
            Odometry, '/diff_drive/odometry', self.odom_callback, 10
        )
        
        # State tracking
        self.start_position = None
        self.current_position = None
        self.start_time = None
        self.distance_traveled = 0.0
        self.moving = False
        self.finished = False
        
        # Timer to send velocity commands
        self.create_timer(0.1, self.control_loop)

    def odom_callback(self, msg: Odometry):
        """Handle odometry updates to track distance traveled."""
        if self.start_position is None:
            # Initialize start position on first message
            self.start_position = (
                msg.pose.pose.position.x,
                msg.pose.pose.position.y,
                msg.pose.pose.position.z
            )
            self.start_time = self.get_clock().now()
            self.get_logger().info(f'Start position: {self.start_position}')
            self.moving = True
        
        # Update current position
        self.current_position = (
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            msg.pose.pose.position.z
        )
        
        # Calculate distance traveled
        dx = self.current_position[0] - self.start_position[0]
        dy = self.current_position[1] - self.start_position[1]
        dz = self.current_position[2] - self.start_position[2]
        self.distance_traveled = sqrt(dx*dx + dy*dy + dz*dz)
        
        self.get_logger().debug(
            f'Distance traveled: {self.distance_traveled:.4f}m'
        )

    def control_loop(self):
        """Main control loop to manage robot velocity command."""
        if not self.moving:
            return
        
        if self.distance_traveled >= self.target_distance:
            # Stop the robot
            twist = Twist()
            self.cmd_vel_pub.publish(twist)
            
            if not self.finished:
                end_time = self.get_clock().now()
                elapsed_time_sec = (end_time - self.start_time).nanoseconds / 1e9
                
                self.get_logger().info('='*50)
                self.get_logger().info('MOVEMENT COMPLETE')
                self.get_logger().info('='*50)
                self.get_logger().info(f'Target distance: {self.target_distance:.4f}m')
                self.get_logger().info(f'Actual distance: {self.distance_traveled:.4f}m')
                self.get_logger().info(f'Target velocity: {self.velocity:.4f}m/s')
                self.get_logger().info(f'Time elapsed: {elapsed_time_sec:.4f}s')
                self.get_logger().info(f'Actual avg velocity: {self.distance_traveled/elapsed_time_sec:.4f}m/s')
                self.get_logger().info('='*50)
                
                self.moving = False
                self.finished = True
        else:
            # Send velocity command
            twist = Twist()
            twist.linear.x = self.velocity
            self.cmd_vel_pub.publish(twist)


def main(args=None):
    rclpy.init(args=args)
    node = DistanceController()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('Shutting down...')
    finally:
        # Stop the robot
        twist = Twist()
        node.cmd_vel_pub.publish(twist)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
