"""Host only: Gazebo, its ROS adapter, command gateway, and instrumentation."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, TimerAction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    get = lambda key: LaunchConfiguration(key).perform(context)
    args = ['ign', 'gazebo']
    if get('paused').lower() != 'true':
        args += ['-r']
    if get('gui').lower() != 'true':
        args += ['-s', '--headless-rendering']
    sim = ExecuteProcess(cmd=args + [get('world')], output='screen')
    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', arguments=[
        '/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
        '/scan@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan',
        '/ground_truth@nav_msgs/msg/Odometry[ignition.msgs.Odometry',
        '/cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist'],
        remappings=[('/scan', '/robot/scan')], output='screen')
    collector = Node(package='wall_follow_benchmark', executable='collector', output='screen',
        parameters=[dict(parameters_file=get('parameters_file'), output_dir=get('output_dir'),
                         duration=float(get('duration')),
                         command_timeout=float(get('command_timeout')))])
    handlers = [RegisterEventHandler(OnProcessExit(target_action=process,
        on_exit=[EmitEvent(event=Shutdown(reason=reason))])) for process, reason in (
            (collector, 'Host collection ended'), (sim, 'Simulator exited'), (bridge, 'Gazebo adapter exited'))]
    return [*handlers, collector, bridge, sim,
        TimerAction(period=float(get('wall_timeout')),
                    actions=[EmitEvent(event=Shutdown(reason='Wall-clock watchdog expired'))])]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('world'),
        DeclareLaunchArgument('paused', default_value='false'),
        DeclareLaunchArgument('parameters_file'),
        DeclareLaunchArgument('output_dir'),
        DeclareLaunchArgument('duration', default_value='120'),
        DeclareLaunchArgument('command_timeout', default_value='1.0'),
        DeclareLaunchArgument('gui', default_value='false'),
        DeclareLaunchArgument('wall_timeout', default_value='600'),
        OpaqueFunction(function=start)])
