"""Paused Gazebo and benchmark; the session supervisor owns the universal bridge."""
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    share = Path(get_package_share_directory("wall_follow_bridge"))
    benchmark = Path(get_package_share_directory("wall_follow_benchmark"))
    defaults = {"world": str(share / "config/world.sdf"),
                "parameters_file": str(share / "config/controller.yaml"),
                "output_dir": "/tmp/wall-follow-session", "duration": "120",
                "command_timeout": "1.0", "gui": "false", "wall_timeout": "900"}
    return LaunchDescription([
        *[DeclareLaunchArgument(name, default_value=value) for name, value in defaults.items()],
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(benchmark / "launch/host.launch.py")),
            launch_arguments={**{name: LaunchConfiguration(name) for name in defaults},
                              "paused": "true"}.items()),
    ])
