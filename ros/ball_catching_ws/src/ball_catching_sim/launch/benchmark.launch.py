"""Application-owned gem5 launch; all robot computation lives in the guest."""
import json
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, ExecuteProcess, IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    output = Path(LaunchConfiguration('output').perform(context)).resolve()
    path = Path(LaunchConfiguration('execution').perform(context)).resolve()
    execution = json.loads(path.read_text())
    root = Path(execution['gem5_root'])
    # Installed launch files are under install/PACKAGE/share/PACKAGE/launch;
    # the suite supplies the source script directory explicitly.
    scripts = Path(execution['scripts'])
    universal = Path(get_package_share_directory('chimaera_ros_bridge'))
    options = {**execution['options'], **execution['rates'], 'robot': False,
               'image_age_periods': execution['fixed']['image_age_periods'],
               'image_diagnostics': execution['fixed'].get('image_diagnostics', False),
               'detailed_timing_socket': '/tmp/chimaera_time.sock',
               'checkpoint_gate': bool(execution.get('checkpoint_prepare') or execution.get('checkpoint_restore')),
               'paused': True, 'gui': False, 'partition': 'ball-memory-suite',
               'transport_ip': '127.0.0.1'}
    options.pop('name', None)
    launch_args = {key: json.dumps(value) if isinstance(value, (list, bool)) else str(value)
                   for key, value in options.items()}
    application = IncludeLaunchDescription(PythonLaunchDescriptionSource(str(
        Path(get_package_share_directory('ball_catching_sim')) / 'launch/application.launch.py')),
        launch_arguments=launch_args.items())
    simulator = ExecuteProcess(cmd=[str(root / 'build/X86/gem5.opt'),
        '--outdir=' + str(output / 'gem5'), '-p', str(universal / 'config'),
        str(scripts / 'gem5_ball_catching.py'), '--gem5-root', str(root),
        '--experiment', str(path), '--bundle', execution['bundle'],
        '--transport-backend', execution['fixed'].get('transport_backend', 'm5'),
        '--image', execution['image'], '--kernel', execution['kernel'], '--managed-shutdown',
        '--guest-file', '/tmp/chimaera-ball-session.json=' + str(output / 'guest-session.json'),
        '--guest-file', '/tmp/chimaera-ball.urdf=' + str(output / 'robot.urdf'),
        '--guest-file', '/tmp/chimaera-ball-start=' + str(output / 'start-guest.sh')],
        output='screen', sigterm_timeout='10')
    fixed = execution['fixed']
    checkpoint_parameters = {}
    if execution.get('checkpoint_prepare'):
        checkpoint_parameters = dict(checkpoint_capture=str(output / 'host-replay.bin'),
                                     checkpoint_output=str(output))
    if execution.get('checkpoint_restore'):
        checkpoint = Path(execution['checkpoint_restore'])
        checkpoint_parameters = dict(checkpoint_replay=str(checkpoint / 'host-replay.bin'),
            checkpoint_directory=str(checkpoint), checkpoint_output=str(output),
            pressure_warmup_ns=round(fixed['pressure_warmup'] * 1e9))
    bridge = Node(package='chimaera_ros_bridge', executable='gazebo_host_bridge', output='screen',
        additional_env={'IGN_PARTITION': options['partition'], 'IGN_IP': options['transport_ip'],
                        'CHIMAERA_DDS_SOCKET': ''},
        parameters=[dict(transport_backend=fixed.get('transport_backend', 'm5'),
            middleware_transport=fixed.get('middleware_transport', 'bridge'),
            config_file=execution['routes'], timing_socket='/tmp/chimaera_time.sock',
            timing_file=str(output / 'timing.csv'), gazebo_world='ball_catching',
            interval_us=fixed['interval_us'], poll_us=fixed['poll_us'],
            physics_step_ns=round(fixed['physics_step'] * 1e9), startup_timeout_s=fixed['startup_timeout_s'],
            max_serialized_bytes=fixed['camera_width'] * fixed['camera_height'] * 3 + 4096,
            max_pending_messages=16, status_bar=False, **checkpoint_parameters)])
    return [application, bridge, simulator, *[RegisterEventHandler(OnProcessExit(target_action=process,
        on_exit=[EmitEvent(event=Shutdown(reason='Benchmark component exited'))])) for process in (bridge, simulator)]]


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('execution'), DeclareLaunchArgument('output'),
                              OpaqueFunction(function=start)])
