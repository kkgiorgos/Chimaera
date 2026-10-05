"""Native local FR3 catching application, with replaceable robot compute."""
import json
import os
from pathlib import Path
import tempfile
from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler)
from launch.event_handlers import OnProcessExit, OnShutdown
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ball_catching_sim.scene import generate, NEUTRAL


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    output = str(Path(get('output')).resolve()) if get('output') else ''
    if output:
        Path(output).mkdir(parents=True, exist_ok=True)
        if (Path(output) / 'experiment.json').exists():
            raise ValueError('Use a fresh output directory to preserve previous trial records')
    temporary = tempfile.TemporaryDirectory(prefix='ball-catching-')
    scene_directory = output or temporary.name
    options = dict(initial_pose=json.loads(get('initial_pose')),
                   launch_position=json.loads(get('launch_position')),
                   launch_direction=json.loads(get('launch_direction')),
                   launch_speed=float(get('launch_speed')), retention=float(get('retention')),
                   camera_hz=float(get('camera_hz')), physics_step=float(get('physics_step')),
                   cup_radius=float(get('cup_radius')), cup_depth=float(get('cup_depth')),
                   auto_throw=get('auto_throw').lower() == 'true',
                   trial_timeout=float(get('trial_timeout')), output=output)
    world, robot_file, urdf, calibration = generate(scene_directory, **options)
    if output:
        (Path(output) / 'experiment.json').write_text(json.dumps(options, indent=2) + '\n')
    plugin_path = str(Path(get_package_prefix('ball_catching_sim')) / 'lib')
    env = {'IGN_GAZEBO_SYSTEM_PLUGIN_PATH': plugin_path + ':' + os.environ.get('IGN_GAZEBO_SYSTEM_PLUGIN_PATH', ''),
           'IGN_PARTITION': get('partition')}
    command = ['ign', 'gazebo', '-r']
    if get('gui').lower() != 'true':
        command += ['-s', '--headless-rendering']
    gazebo = ExecuteProcess(cmd=[*command, str(world)], additional_env=env, output='screen')
    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', arguments=[
        '/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
        '/stereo/left/image_raw@sensor_msgs/msg/Image[ignition.msgs.Image',
        '/stereo/right/image_raw@sensor_msgs/msg/Image[ignition.msgs.Image'],
        additional_env={'IGN_PARTITION': get('partition')}, output='screen')
    actions = [RegisterEventHandler(OnShutdown(on_shutdown=[OpaqueFunction(
        function=lambda context: temporary.cleanup())])), bridge, gazebo,
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             parameters=[{'robot_description': urdf, 'use_sim_time': True}],
             remappings=[('/joint_states', '/robot/joint_states')], output='screen')]
    if get('robot').lower() == 'true':
        share = Path(get_package_share_directory('ball_catching_robot'))
        actions.append(IncludeLaunchDescription(PythonLaunchDescriptionSource(
            str(share / 'launch/robot.launch.py')), launch_arguments={
                'robot_file': str(robot_file), 'calibration': json.dumps(calibration),
                'initial_pose': get('initial_pose'), 'output': output}.items()))
    for process in [gazebo, bridge]:
        actions.append(RegisterEventHandler(OnProcessExit(target_action=process,
            on_exit=[EmitEvent(event=Shutdown(reason='Simulation component exited'))])))
    return actions


def generate_launch_description():
    defaults = dict(gui='true', robot='true', output='', partition='ball-catching',
                    initial_pose=json.dumps(NEUTRAL), launch_position='[2.08,0.04,2.0]',
                    launch_direction='[-1.0,0.0,0.0]', launch_speed='3.0', retention='1.0',
                    camera_hz='90.0', physics_step='0.001', cup_radius='0.12', cup_depth='0.14',
                    auto_throw='true', trial_timeout='4.0')
    return LaunchDescription([*[DeclareLaunchArgument(k, default_value=v) for k, v in defaults.items()],
                              OpaqueFunction(function=start)])
