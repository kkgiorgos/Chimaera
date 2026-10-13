"""Robot compute only. No simulator, throw configuration, or scoring inputs."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import json
import math


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    common = {'use_sim_time': get('use_sim_time').lower() == 'true',
              'robot_file': get('robot_file'), 'output': get('output'),
              'initial_pose': None,
              'gem5_clock_library': get('gem5_clock_library'),
              'gem5_time_origin_file': get('gem5_time_origin_file'),
              'record_telemetry': get('record_telemetry').lower() == 'true'}
    upright = [0., 0., 0., -.14, 0., .50, math.pi/4]
    common['initial_pose'] = json.loads(get('initial_pose')) if get('initial_pose') != 'auto' else upright
    if len(common['initial_pose']) != 7 or any(
            not math.isfinite(q) or abs(q-home) > 1e-8
            for q, home in zip(common['initial_pose'], upright)):
        raise ValueError('Robot must start from the fixed upright home pose')
    common['initial_pose'] = upright
    calibration = json.loads(get('calibration'))
    nodes = [Node(package='ball_catching_robot', executable='perception',
                 parameters=[{'use_sim_time': common['use_sim_time'], 'output': common['output'],
                              **calibration, 'camera_hz': float(get('camera_hz')),
                              'image_age_periods': float(get('image_age_periods')),
                              'image_diagnostics': get('image_diagnostics').lower() == 'true',
                              'gem5_clock_library': get('gem5_clock_library'),
                              'gem5_time_origin_file': common['gem5_time_origin_file'],
                              'record_telemetry': common['record_telemetry']}], output='screen'),
            Node(package='ball_catching_robot', executable='intercept',
                 parameters=[{**common, 'planner_hz': float(get('planner_hz')),
                              'plan_interval': float(get('plan_interval'))}],
                 output='screen'),
            Node(package='ball_catching_robot', executable='effort_control',
                 parameters=[{**common, 'control_hz': float(get('control_hz'))}],
                 output='screen')]
    return [*nodes, *[RegisterEventHandler(OnProcessExit(target_action=node,
        on_exit=[EmitEvent(event=Shutdown(reason='Robot component exited'))])) for node in nodes]]


def generate_launch_description():
    defaults = dict(robot_file='', output='', use_sim_time='true', control_hz='250',
                    planner_hz='30', plan_interval='0.06',
                    camera_hz='90', image_age_periods='2',
                    image_diagnostics='false', record_telemetry='false',
                    gem5_clock_library='', gem5_time_origin_file='',
                    initial_pose='auto',
                    calibration='{"focal":400.0,"cx":320.0,"cy":240.0,"baseline":0.3,"camera_origin":[-0.65,0.15,1.0]}')
    return LaunchDescription([*[DeclareLaunchArgument(k, default_value=v) for k, v in defaults.items()],
                              OpaqueFunction(function=start)])
