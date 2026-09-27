"""Run only the robot computer workload. No host dependencies or shared filesystem."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    get = LaunchConfiguration
    return LaunchDescription([
        DeclareLaunchArgument('parameters_file'),
        DeclareLaunchArgument('use_sim_time', default_value='true'),
        DeclareLaunchArgument('scan_topic', default_value='/robot/scan'),
        DeclareLaunchArgument('command_topic', default_value='/robot/cmd_vel'),
        DeclareLaunchArgument('clock_topic', default_value='/clock'),
        Node(package='wall_follow_robot', executable='controller', output='screen',
             parameters=[get('parameters_file'),
                         {'use_sim_time': ParameterValue(get('use_sim_time'), value_type=bool)}],
             remappings=[('scan', get('scan_topic')), ('cmd_vel', get('command_topic')),
                         ('/clock', get('clock_topic'))])])
