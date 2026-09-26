import json
import math
from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, TimerAction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from wall_follow_benchmark.core import DEFAULTS, validate
from wall_follow_benchmark.world import make_world


def start(context):
    get = lambda k: LaunchConfiguration(k).perform(context)
    params = {k: type(v)(get(k)) for k,v in DEFAULTS.items()}
    validate(params)
    for key in ('duration', 'wall_timeout'):
        value = float(get(key))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key} must be finite and positive')
    if get('gui').lower() not in ('true', 'false'):
        raise ValueError('gui must be true or false')
    output = Path(get('output_dir')).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Do not overwrite an existing experiment.
    if (output/'samples.csv').exists():
        raise RuntimeError(f'Run already exists: {output}; choose a new output_dir')
    sensor = dict(lidar_hz=float(get('lidar_hz')), lidar_samples=int(get('lidar_samples')),
                  noise_std=float(get('noise_std')), physics_step=float(get('physics_step')))
    arena = dict(arena_width=float(get('arena_width')), arena_height=float(get('arena_height')))
    world = output/'world.sdf'
    world.write_text(make_world(**sensor, **arena))
    (output/'experiment.json').write_text(json.dumps(dict(sensor=sensor, arena=arena,
        architecture=get('architecture'), gui=get('gui')), indent=2))
    args = ['ign', 'gazebo', '-r']
    if get('gui').lower() != 'true':
        args += ['-s', '--headless-rendering']
    sim = ExecuteProcess(cmd=args+[str(world)], output='screen')
    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', arguments=[
        '/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
        '/scan@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan',
        '/ground_truth@nav_msgs/msg/Odometry[ignition.msgs.Odometry',
        '/cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist'], output='screen')
    controller = Node(package='wall_follow_benchmark', executable='controller', output='screen',
        parameters=[params, arena, dict(use_sim_time=True, duration=float(get('duration')), output_dir=str(output))])
    stop = RegisterEventHandler(OnProcessExit(target_action=controller,
        on_exit=[EmitEvent(event=Shutdown(reason='Benchmark controller exited'))]))
    sim_stop = RegisterEventHandler(OnProcessExit(target_action=sim,
        on_exit=[EmitEvent(event=Shutdown(reason='Simulator exited'))]))
    return [stop, sim_stop, sim, bridge, controller, TimerAction(period=float(get('wall_timeout')),
        actions=[EmitEvent(event=Shutdown(reason='Wall-clock watchdog expired'))])]


def generate_launch_description():
    defaults = dict(DEFAULTS, duration=120., output_dir='results/run', gui='true',
                    lidar_hz=20., lidar_samples=720, noise_std=0., physics_step=.001,
                    architecture='unspecified', wall_timeout=600., arena_width=12., arena_height=8.)
    return LaunchDescription([DeclareLaunchArgument(k, default_value=str(v)) for k,v in defaults.items()]
                             + [OpaqueFunction(function=start)])
