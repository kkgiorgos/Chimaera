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
              'initial_pose': json.loads(get('initial_pose'))}
    upright = [0., -math.pi/4, 0., -3*math.pi/4, 0., math.pi/2, math.pi/4]
    if len(common['initial_pose']) != 7 or any(
            not math.isfinite(q) or abs(q-home) > 1e-8
            for q, home in zip(common['initial_pose'], upright)):
        raise ValueError('Robot must start from the fixed upright home pose')
    common['initial_pose'] = upright
    gripper = get('mode') == 'gripper'
    calibration = json.loads(get('calibration'))
    nodes = [Node(package='ball_catching_robot', executable='perception',
                 parameters=[{'use_sim_time': common['use_sim_time'], 'output': common['output'],
                              'estimate_drag': gripper, **calibration}], output='screen'),
            Node(package='ball_catching_robot', executable='intercept',
                 parameters=[{**common, 'present': get('present').lower() == 'true',
                              'gripper_motor_lead': float(get('gripper_motor_lead')),
                              'presentation_hold': float(get('presentation_hold'))}],
                 output='screen'),
            Node(package='ball_catching_robot', executable='effort_control', parameters=[common],
                 output='screen')]
    if gripper:
        nodes.append(Node(package='ball_catching_robot', executable='gripper_control',
                          parameters=[{'use_sim_time': common['use_sim_time'], 'output': common['output']}],
                          output='screen'))
    return [*nodes, *[RegisterEventHandler(OnProcessExit(target_action=node,
        on_exit=[EmitEvent(event=Shutdown(reason='Robot component exited'))])) for node in nodes]]


def generate_launch_description():
    defaults = dict(robot_file='', output='', use_sim_time='true', mode='cup', present='false', gripper_motor_lead='0.0', presentation_hold='0.25',
                    initial_pose='[0.0,-0.7853981634,0.0,-2.3561944902,0.0,1.5707963268,0.7853981634]',
                    calibration='{"focal":400.0,"cx":320.0,"cy":240.0,"baseline":0.3,"camera_origin":[-0.65,0.15,1.0]}')
    return LaunchDescription([*[DeclareLaunchArgument(k, default_value=v) for k, v in defaults.items()],
                              OpaqueFunction(function=start)])
