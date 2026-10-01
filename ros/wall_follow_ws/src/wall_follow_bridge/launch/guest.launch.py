"""Unmodified robot workload launched in the isolated guest ROS domain."""
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    share = Path(get_package_share_directory("wall_follow_bridge"))
    robot = Path(get_package_share_directory("wall_follow_robot"))
    injected = Path("/tmp/chimaera-controller.yaml")
    parameters = str(injected if injected.is_file() else share / "config/controller.yaml")
    return LaunchDescription([
        RegisterEventHandler(OnProcessExit(
            on_exit=[EmitEvent(event=Shutdown(reason="Guest robot exited"))])),
        DeclareLaunchArgument("parameters_file", default_value=parameters),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(robot / "launch/robot.launch.py")),
            launch_arguments={"parameters_file": LaunchConfiguration("parameters_file")}.items()),
    ])
