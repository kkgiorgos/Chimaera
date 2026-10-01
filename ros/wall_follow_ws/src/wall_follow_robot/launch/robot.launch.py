"""Run only the robot computer workload. No host dependencies or shared filesystem."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    get = LaunchConfiguration
    return LaunchDescription([
        DeclareLaunchArgument('parameters_file'),
        DeclareLaunchArgument('scan_topic', default_value='/robot/scan'),
        DeclareLaunchArgument('command_topic', default_value='/robot/cmd_vel'),
        Node(package='wall_follow_robot', executable='controller', output='screen',
             parameters=[get('parameters_file'),
                         {'use_sim_time': False}],
             remappings=[('scan', get('scan_topic')), ('cmd_vel', get('command_topic'))])])
