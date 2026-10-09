"""Native local FR3 catching application, with replaceable robot compute."""
import json
import math
import os
from pathlib import Path
import tempfile
import subprocess
from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler)
from launch.event_handlers import OnProcessExit, OnShutdown
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ball_catching_sim.scene import generate, robot_urdf, home_pose
from ball_catching_sim.flight import aimed_throw, bounced_throw, drag_factor, court_net_height, COURT_LENGTH, NET_HEIGHT


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    output = str(Path(get('output')).resolve()) if get('output') else ''
    if output:
        Path(output).mkdir(parents=True, exist_ok=True)
        if (Path(output) / 'experiment.json').exists():
            raise ValueError('Use a fresh output directory to preserve previous trial records')
    temporary = tempfile.TemporaryDirectory(prefix='ball-catching-')
    scene_directory = output or temporary.name
    mode = get('mode')
    home = home_pose(mode)
    initial_pose = json.loads(get('initial_pose')) if get('initial_pose') != 'auto' else home
    validator = Path(get_package_prefix('ball_catching_robot')) / 'lib/ball_catching_robot/validate_model'
    orientation = get('home_orientation')
    if orientation not in ('upward', 'forward'):
        raise ValueError('home_orientation must be upward or forward')
    def target_arguments(target):
        if get('home_pitch') != 'auto':
            return ['--pitched-target', get('home_pitch'), *map(str, target)]
        return ['--upward-target' if orientation == 'upward' else '--target', *map(str, target)]
    if len(initial_pose) != 7 or any(not math.isfinite(q) or abs(q-home_angle) > 1e-8
                                     for q, home_angle in zip(initial_pose, home)):
        raise ValueError('Every trial must start from the fixed upright home pose')
    initial_pose = home
    options = dict(initial_pose=initial_pose,
                   launch_position=json.loads(get('launch_position')),
                   launch_direction=json.loads(get('launch_direction')),
                   launch_speed=float(get('launch_speed')), retention=float(get('retention')),
                   camera_hz=float(get('camera_hz')), physics_step=float(get('physics_step')),
                   camera_width=int(get('camera_width')), camera_height=int(get('camera_height')),
                   cup_radius=float(get('cup_radius')), cup_depth=float(get('cup_depth')),
                   auto_throw=get('auto_throw').lower() == 'true',
                   trial_timeout=float(get('trial_timeout')), output=output, mode=mode,
                   court=get('court').lower() == 'true', drag_coefficient=float(get('drag_coefficient')),
                   air_density=float(get('air_density')), grip_friction=float(get('grip_friction')),
                   present=get('present').lower() == 'true', allowed_bounces=int(get('allowed_bounces')),
                   court_restitution=float(get('court_restitution')),
                   court_tangent_ratio=float(get('court_tangent_ratio')),
                   camera_position=json.loads(get('camera_position')))
    planned = None
    if get('aimed').lower() == 'true':
        target = json.loads(get('target'))
        if mode == 'gripper':
            model_path = Path(scene_directory) / 'home_model.urdf'
            model_path.write_text(robot_urdf(mode=mode))
            check = subprocess.run([str(validator), str(model_path),
                                    *target_arguments(target)],
                                   capture_output=True, text=True)
            if check.returncode:
                raise ValueError(check.stderr.strip())
        net_height = court_net_height(options['launch_position'], target) if options['court'] else NET_HEIGHT
        bounce_options = dict(bounces=options['allowed_bounces'], restitution=options['court_restitution'],
                              tangent_ratio=options['court_tangent_ratio']) if options['allowed_bounces'] else {}
        planned = (bounced_throw if options['allowed_bounces'] else aimed_throw)(options['launch_position'], target, options['launch_speed'],
                              drag=drag_factor(options['drag_coefficient'], options['air_density']),
                              net_x=COURT_LENGTH/2 if options['court'] else None, net_height=net_height,
                              arc=get('arc'), **bounce_options)
        options['launch_direction'] = list(planned.direction)
    world, robot_file, urdf, calibration = generate(scene_directory, **options)
    if output:
        record = {**options, 'start_condition': 'vertical_home' if mode == 'cup' else 'upright_home',
                  'arc': get('arc') if planned else None, 'target_pitch': get('home_pitch'),
                  'gripper_motor_lead': float(get('gripper_motor_lead')),
                  'presentation_hold': max(.25, options['retention']+.05),
                  'target': json.loads(get('target')) if planned else None,
                  'predicted_flight_time': planned.flight_time if planned else None,
                  'predicted_arrival_speed': planned.arrival_speed if planned else None,
                  'predicted_bounces': planned.bounce_events if planned else []}
        (Path(output) / 'experiment.json').write_text(json.dumps(record, indent=2) + '\n')
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
                'initial_pose': json.dumps(initial_pose), 'output': output,
                'mode': mode, 'present': get('present'), 'gripper_motor_lead': get('gripper_motor_lead'),
                'presentation_hold': str(max(.25, options['retention']+.05))}.items()))
    for process in [gazebo, bridge]:
        actions.append(RegisterEventHandler(OnProcessExit(target_action=process,
            on_exit=[EmitEvent(event=Shutdown(reason='Simulation component exited'))])))
    return actions


def generate_launch_description():
    defaults = dict(gui='true', robot='true', output='', partition='ball-catching',
                    initial_pose='auto', home_orientation='upward', home_pitch='auto', launch_position='[0.5,0.0,1.1]',
                    launch_direction='[0.0,0.0,1.0]', launch_speed='1.5', retention='0.2',
                    camera_hz='90.0', camera_width='640', camera_height='480',
                    physics_step='0.001', cup_radius='0.12', cup_depth='0.14',
                    auto_throw='true', trial_timeout='8.0', mode='gripper', court='false', aimed='true',
                    target='[0.5,0.0,0.8]', arc='high', drag_coefficient='0.55', air_density='1.225',
                    grip_friction='1.0', present='false', gripper_motor_lead='0.0', allowed_bounces='0',
                    court_restitution='0.745', court_tangent_ratio='0.6', camera_position='[-0.65,0.0,1.0]')
    return LaunchDescription([*[DeclareLaunchArgument(k, default_value=v) for k, v in defaults.items()],
                              OpaqueFunction(function=start)])
