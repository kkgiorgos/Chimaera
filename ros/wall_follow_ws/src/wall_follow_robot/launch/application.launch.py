"""Local development: generated world, Gazebo adapter, and controller in one launch."""
from pathlib import Path
import tempfile

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler)
from launch.event_handlers import OnProcessExit, OnShutdown
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from wall_follow_sim.world import make_world


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    actions = []
    world = get('world')
    if not world:
        temporary = tempfile.TemporaryDirectory(prefix='wall-follow-world-')
        world = str(Path(temporary.name) / 'world.sdf')
        Path(world).write_text(make_world(**{
            name: (int(get(name)) if name == 'lidar_samples' else float(get(name)))
            for name in ('lidar_hz', 'lidar_samples', 'noise_std', 'physics_step',
                         'arena_width', 'arena_height')}))
        actions.append(RegisterEventHandler(OnShutdown(
            on_shutdown=[OpaqueFunction(function=lambda context: temporary.cleanup())])))
    command = ['ign', 'gazebo']
    if get('paused').lower() != 'true':
        command.append('-r')
    if get('gui').lower() != 'true':
        command += ['-s', '--headless-rendering']
    gazebo = ExecuteProcess(cmd=[*command, world], output='screen')
    adapter = Node(package='ros_gz_bridge', executable='parameter_bridge', arguments=[
        '/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
        '/scan@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan',
        '/ground_truth@nav_msgs/msg/Odometry[ignition.msgs.Odometry',
        '/cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist'],
        remappings=[('/scan', '/robot/scan')],
        output='screen')
    for process in (gazebo, adapter):
        actions.append(RegisterEventHandler(OnProcessExit(target_action=process,
            on_exit=[EmitEvent(event=Shutdown(reason='Application component exited'))])))
    actions += [adapter, gazebo]
    if get('controller').lower() == 'true':
        share = Path(get_package_share_directory('wall_follow_robot'))
        actions.append(IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(share / 'launch/robot.launch.py')),
            launch_arguments={'parameters_file': get('parameters_file')}.items()))
    return actions


def generate_launch_description():
    defaults = dict(world='', parameters_file='', gui='true', controller='true', paused='false',
                    lidar_hz='20', lidar_samples='720', noise_std='0', physics_step='0.001',
                    arena_width='12', arena_height='8')
    return LaunchDescription([
        *[DeclareLaunchArgument(name, default_value=value) for name, value in defaults.items()],
        OpaqueFunction(function=start)])
