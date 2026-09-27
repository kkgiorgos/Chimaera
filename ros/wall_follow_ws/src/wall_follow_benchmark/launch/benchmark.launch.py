"""Launch runtime processes using inputs prepared by the external benchmark suite."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, TimerAction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    get = lambda k: LaunchConfiguration(k).perform(context)
    world = get('world')
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
        parameters=[get('parameters_file'), dict(use_sim_time=True, output_dir=get('output_dir'))])
    stop = RegisterEventHandler(OnProcessExit(target_action=controller,
        on_exit=[EmitEvent(event=Shutdown(reason='Benchmark controller exited'))]))
    sim_stop = RegisterEventHandler(OnProcessExit(target_action=sim,
        on_exit=[EmitEvent(event=Shutdown(reason='Simulator exited'))]))
    return [stop, sim_stop, sim, bridge, controller, TimerAction(period=float(get('wall_timeout')),
        actions=[EmitEvent(event=Shutdown(reason='Wall-clock watchdog expired'))])]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('world'),
        DeclareLaunchArgument('parameters_file'),
        DeclareLaunchArgument('output_dir'),
        DeclareLaunchArgument('gui', default_value='false'),
        DeclareLaunchArgument('wall_timeout', default_value='600'),
        OpaqueFunction(function=start)])
