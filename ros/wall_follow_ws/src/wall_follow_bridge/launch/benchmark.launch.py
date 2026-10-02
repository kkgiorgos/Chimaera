"""Complete Chimaera benchmark attempt, owned by the suite runner."""
import json
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler,
                            SetEnvironmentVariable)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from wall_follow_benchmark.hardware import DEFAULTS as HARDWARE_DEFAULTS

# The session file also stages the guest; keep transport defaults in one place.
BRIDGE_KEYS = ('max_serialized_bytes', 'max_pending_messages', 'interval_us', 'poll_us',
               'steps', 'gazebo_world', 'physics_step_ns', 'startup_timeout_s',
               'status_bar', 'report_seconds')


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    share = Path(get_package_share_directory('wall_follow_bridge'))
    session = json.loads((share / 'config/session.json').read_text())
    root = Path(get('gem5_root')).resolve()
    command = [str(root / 'build/X86/gem5.opt'), '--outdir=' + get('outdir'),
               '-p', str(Path(get_package_share_directory('chimaera_ros_bridge')) / 'config'),
               str(share / 'config/gem5_wall_follow.py'), '--gem5-root', str(root),
               '--socket-path', get('timing_socket'), '--managed-shutdown',
               '--guest-command', session['deploy']['guest_root'] + '/guest_start',
               '--controller-file', get('parameters_file')]
    for name in HARDWARE_DEFAULTS:
        command += ['--' + name.replace('_', '-'), get(name)]
    for name in ('image', 'kernel'):
        if get(name):
            command += ['--' + name, get(name)]
    simulator = ExecuteProcess(cmd=command, output='screen', sigterm_timeout='10')
    parameters = {}
    for name in BRIDGE_KEYS:
        default = session['bridge'][name]
        parameters[name] = get(name).lower() == 'true' if type(default) is bool else type(default)(get(name))
    parameters.update(config_file=str(share / 'config/bridge.json'),
                      timing_socket=get('timing_socket'),
                      timing_file=str(Path(get('output_dir')) / 'timing.csv'))
    bridge = Node(package='chimaera_ros_bridge', executable='gazebo_host_bridge',
                  parameters=[parameters], output='screen')
    benchmark = Path(get_package_share_directory('wall_follow_benchmark')) / 'launch/benchmark.launch.py'
    actions = [RegisterEventHandler(OnProcessExit(target_action=process,
                    on_exit=[EmitEvent(event=Shutdown(reason='Chimaera component exited'))]))
               for process in (bridge, simulator)]
    actions += [IncludeLaunchDescription(PythonLaunchDescriptionSource(str(benchmark)),
                    launch_arguments={**{name: get(name) for name in
                        ('world', 'parameters_file', 'output_dir', 'duration', 'command_timeout',
                         'gui', 'wall_timeout')}, 'controller': 'false', 'paused': 'true'}.items()),
                bridge, simulator]
    return actions


def generate_launch_description():
    share = Path(get_package_share_directory('wall_follow_bridge'))
    session = json.loads((share / 'config/session.json').read_text())
    defaults = {name: session['bridge'][name] for name in BRIDGE_KEYS}
    defaults.update(HARDWARE_DEFAULTS, image='', kernel='', timing_socket='/tmp/chimaera_time.sock',
                    duration=120, command_timeout=1.0, gui=False, wall_timeout=600)
    return LaunchDescription([
        *[DeclareLaunchArgument(name) for name in
          ('world', 'parameters_file', 'output_dir', 'gem5_root', 'outdir')],
        *[DeclareLaunchArgument(name, default_value=str(value).lower() if type(value) is bool else str(value))
          for name, value in defaults.items()],
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', '1'),
        SetEnvironmentVariable('ROS_DOMAIN_ID', str(session['host']['domain_id'])),
        OpaqueFunction(function=start)])
