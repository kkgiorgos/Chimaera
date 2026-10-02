"""One benchmark attempt: application plus a passive collector and lifecycle limits."""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, IncludeLaunchDescription,
                            OpaqueFunction, RegisterEventHandler, TimerAction)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    collector = Node(package='wall_follow_benchmark', executable='collector', output='screen',
        parameters=[dict(parameters_file=get('parameters_file'), output_dir=get('output_dir'),
                         duration=float(get('duration')),
                         command_timeout=float(get('command_timeout')))])
    application = Path(get_package_share_directory('wall_follow_robot')) / 'launch/application.launch.py'
    return [RegisterEventHandler(OnProcessExit(target_action=collector,
                on_exit=[EmitEvent(event=Shutdown(reason='Collection ended'))])),
            collector,
            IncludeLaunchDescription(PythonLaunchDescriptionSource(str(application)),
                launch_arguments={name: get(name) for name in
                    ('world', 'parameters_file', 'gui', 'controller', 'paused')}.items()),
            TimerAction(period=float(get('wall_timeout')),
                actions=[EmitEvent(event=Shutdown(reason='Benchmark wall timeout'))])]


def generate_launch_description():
    defaults = dict(duration='120', command_timeout='1', gui='false', wall_timeout='600',
                    controller='true', paused='false')
    return LaunchDescription([
        *[DeclareLaunchArgument(name) for name in ('world', 'parameters_file', 'output_dir')],
        *[DeclareLaunchArgument(name, default_value=value) for name, value in defaults.items()],
        OpaqueFunction(function=start)])
