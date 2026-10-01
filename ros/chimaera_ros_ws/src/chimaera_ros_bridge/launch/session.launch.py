from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, ExecuteProcess, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackagePrefix


def generate_launch_description():
    runner = ExecuteProcess(cmd=[
        PathJoinSubstitution([FindPackagePrefix('chimaera_ros_bridge'),
                              'lib', 'chimaera_ros_bridge', 'chimaera_ros']),
        LaunchConfiguration('action'), LaunchConfiguration('manifest'),
        '--side', LaunchConfiguration('side')], output='screen', sigterm_timeout='15')
    return LaunchDescription([
        DeclareLaunchArgument('manifest', description='Absolute session manifest path'),
        DeclareLaunchArgument('action', default_value='bringup', choices=['run', 'bringup']),
        DeclareLaunchArgument('side', default_value='host', choices=['host', 'guest']),
        RegisterEventHandler(OnProcessExit(target_action=runner,
            on_exit=[EmitEvent(event=Shutdown(reason='Chimaera session exited'))])),
        runner,
    ])
