"""Local convenience composition. The robot and all instrumentation are separate processes."""
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    get = lambda key: LaunchConfiguration(key).perform(context)
    # Same robot executable and interface as robot.launch.py, with no benchmark parameters.
    robot = Node(package='wall_follow_robot', executable='controller', output='screen',
        parameters=[get('parameters_file'), {'use_sim_time': True}],
        remappings=[('scan', '/robot/scan'), ('cmd_vel', '/robot/cmd_vel')])
    host = Path(get_package_share_directory('wall_follow_benchmark')) / 'launch/host.launch.py'

    arguments = {key: get(key) for key in ('world', 'parameters_file', 'output_dir',
                 'duration', 'command_timeout', 'gui', 'wall_timeout')}
    return [RegisterEventHandler(OnProcessExit(target_action=robot,
                on_exit=[EmitEvent(event=Shutdown(reason='Robot process exited'))])),
            IncludeLaunchDescription(PythonLaunchDescriptionSource(str(host)),
                                     launch_arguments=arguments.items()), robot]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('world'), DeclareLaunchArgument('parameters_file'),
        DeclareLaunchArgument('output_dir'), DeclareLaunchArgument('duration', default_value='120'),
        DeclareLaunchArgument('command_timeout', default_value='1.0'),
        DeclareLaunchArgument('gui', default_value='false'),
        DeclareLaunchArgument('wall_timeout', default_value='600'),
        OpaqueFunction(function=start)])
