"""Controller-only entry point, also used inside the guest."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, OpaqueFunction, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    parameters = [{'use_sim_time': False}]
    if get('parameters_file'):
        parameters.insert(0, get('parameters_file'))
    robot = Node(package='wall_follow_robot', executable='controller', output='screen',
                 parameters=parameters,
                 remappings=[('scan', get('scan_topic')), ('cmd_vel', get('command_topic'))])
    return [RegisterEventHandler(OnProcessExit(target_action=robot,
                on_exit=[EmitEvent(event=Shutdown(reason='Controller exited'))])), robot]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('parameters_file', default_value=''),
        DeclareLaunchArgument('scan_topic', default_value='/robot/scan'),
        DeclareLaunchArgument('command_topic', default_value='/cmd_vel'),
        OpaqueFunction(function=start)])
