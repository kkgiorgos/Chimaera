import sys
from pathlib import Path
from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler, SetEnvironmentVariable
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    defaults = {
        "interval_us": "100000", "poll_us": "10000", "steps": "0",
        "ratio": "1.0", "startup_timeout_s": "300",
        "status_bar": "true", "report_seconds": "1.0",
        "timing_socket": "/tmp/chimaera_time.sock",
        "config_file": str(Path(get_package_share_directory("talker_listener_bridge")) / "config/bridge.json"),
    }
    parameters = {
        name: ParameterValue(LaunchConfiguration(name), value_type=(
            str if name in ("timing_socket", "config_file") else bool if name == "status_bar"
            else float if name in ("ratio", "report_seconds") else int))
        for name in defaults
    }
    nodes = [
        Node(package="talker_listener_bridge", executable="host_bridge",
             parameters=[parameters], output="screen",
             additional_env={"CHIMAERA_STATUS_TTY": "1" if sys.stdout.isatty() else "0"}),
        Node(package="demo_nodes_cpp", executable="talker", name="host_talker",
             remappings=[("chatter", "/host/chatter")], output="screen"),
        Node(package="demo_nodes_cpp", executable="listener", name="host_listener",
             remappings=[("chatter", "/guest/chatter")], output="screen"),
    ]
    return LaunchDescription([
        *[DeclareLaunchArgument(name, default_value=value) for name, value in defaults.items()],
        SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
        SetEnvironmentVariable("ROS_DOMAIN_ID", "41"),
        *[RegisterEventHandler(OnProcessExit(
            target_action=node, on_exit=[EmitEvent(event=Shutdown(reason="host component exited"))]))
          for node in nodes],
        *nodes,
    ])
