"""Complete Gazebo/ROS host; start gem5 separately after this launch is ready."""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution


def generate_launch_description():
    share = Path(get_package_share_directory("wall_follow_bridge"))
    benchmark = Path(get_package_share_directory("wall_follow_benchmark"))
    required = ("world", "parameters_file", "output_dir")
    defaults = {
        "duration": "120", "command_timeout": "1.0", "gui": "false",
        "wall_timeout": "600",
    }
    arguments = {name: LaunchConfiguration(name) for name in (*required, *defaults)}
    return LaunchDescription([
        *[DeclareLaunchArgument(name) for name in required],
        *[DeclareLaunchArgument(name, default_value=value) for name, value in defaults.items()],
        SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
        SetEnvironmentVariable("ROS_DOMAIN_ID", "41"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(benchmark / "launch/host.launch.py")),
            launch_arguments={**arguments, "paused": "true"}.items()),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(share / "launch/bridge.launch.py")),
            launch_arguments={"timing_file": PathJoinSubstitution([LaunchConfiguration("output_dir"), "timing.csv"])}.items()),
    ])
