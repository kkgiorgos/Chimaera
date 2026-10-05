"""Robot compute only. No simulator, throw configuration, or scoring inputs."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import json


def start(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    common = {'use_sim_time': get('use_sim_time').lower() == 'true',
              'robot_file': get('robot_file'), 'output': get('output'),
              'initial_pose': json.loads(get('initial_pose'))}
    calibration = json.loads(get('calibration'))
    numerical_env = {'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    nodes = [Node(package='ball_catching_robot', executable='perception',
                 parameters=[{'use_sim_time': common['use_sim_time'], 'output': common['output'],
                              **calibration}], additional_env=numerical_env, output='screen'),
            Node(package='ball_catching_robot', executable='intercept', parameters=[common],
                 additional_env=numerical_env, output='screen'),
            Node(package='ball_catching_robot', executable='effort_control', parameters=[common],
                 additional_env=numerical_env, output='screen')]
    return [*nodes, *[RegisterEventHandler(OnProcessExit(target_action=node,
        on_exit=[EmitEvent(event=Shutdown(reason='Robot component exited'))])) for node in nodes]]


def generate_launch_description():
    defaults = dict(robot_file='', output='', use_sim_time='true',
                    initial_pose='[0.0,-0.7853981634,0.0,-2.3561944902,0.0,1.5707963268,0.7853981634]',
                    calibration='{"focal":400.0,"cx":320.0,"cy":240.0,"baseline":0.3,"camera_origin":[-0.65,0.15,1.0]}')
    return LaunchDescription([*[DeclareLaunchArgument(k, default_value=v) for k, v in defaults.items()],
                              OpaqueFunction(function=start)])
