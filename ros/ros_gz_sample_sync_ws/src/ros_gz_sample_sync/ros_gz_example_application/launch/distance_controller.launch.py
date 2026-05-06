from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Launch the distance controller node with configurable parameters."""
    
    # Declare launch arguments
    target_distance_arg = DeclareLaunchArgument(
        'target_distance',
        default_value='1.0',
        description='Target distance to travel in meters'
    )
    
    velocity_arg = DeclareLaunchArgument(
        'velocity',
        default_value='0.5',
        description='Desired forward velocity in m/s'
    )
    
    # Create the distance controller node
    distance_controller = Node(
        package='ros_gz_example_application',
        executable='distance_controller.py',
        name='distance_controller',
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'target_distance': LaunchConfiguration('target_distance'),
            'velocity': LaunchConfiguration('velocity'),
        }]
    )
    
    return LaunchDescription([
        target_distance_arg,
        velocity_arg,
        distance_controller,
    ])
