from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def start(context):
    share = Path(get_package_share_directory("wall_follow_bridge"))
    root = Path(LaunchConfiguration("gem5_root").perform(context)).resolve()
    command = [str(root / "build/X86/gem5.opt"),
               "--outdir=" + LaunchConfiguration("outdir").perform(context),
               "-p", str(Path(get_package_share_directory("chimaera_ros_bridge")) / "config"),
               str(share / "config/gem5_wall_follow.py"), "--gem5-root", str(root),
               "--socket-path", LaunchConfiguration("timing_socket").perform(context)]
    command.extend(["--managed-shutdown", "--guest-command", "/opt/chimaera/wall_follow/guest_start",
                    "--controller-file", LaunchConfiguration("parameters_file").perform(context)])
    for name in ("cpu_type", "cpu_clock", "num_cores", "l1d_size", "l1i_size",
                 "l2_size", "l1_assoc", "l2_assoc"):
        command.extend(["--" + name.replace("_", "-"), LaunchConfiguration(name).perform(context)])
    for name in ("image", "kernel"):
        value = LaunchConfiguration(name).perform(context)
        if value:
            command.extend(["--" + name, value])
    simulator = ExecuteProcess(cmd=command, output="screen", sigterm_timeout="10")
    return [RegisterEventHandler(OnProcessExit(
        target_action=simulator,
        on_exit=[EmitEvent(event=Shutdown(reason="gem5 exited"))])), simulator]


def generate_launch_description():
    share = Path(get_package_share_directory("wall_follow_bridge"))
    return LaunchDescription([
        DeclareLaunchArgument("world"),
        DeclareLaunchArgument("parameters_file"),
        DeclareLaunchArgument("output_dir"),
        DeclareLaunchArgument("gem5_root", description="Absolute path to the custom gem5 tree"),
        DeclareLaunchArgument("outdir", default_value="m5out-wall-follow"),
        *[DeclareLaunchArgument(name, default_value=value) for name, value in {
            "cpu_type": "timing", "cpu_clock": "3GHz", "num_cores": "2",
            "l1d_size": "16KiB", "l1i_size": "16KiB", "l2_size": "256KiB",
            "l1_assoc": "8", "l2_assoc": "16",
        }.items()],
        DeclareLaunchArgument("image", default_value=""),
        DeclareLaunchArgument("kernel", default_value=""),
        DeclareLaunchArgument("timing_socket", default_value="/tmp/chimaera_time.sock"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(share / "launch/host.launch.py")),
            launch_arguments={name: LaunchConfiguration(name) for name in
                              ("world", "parameters_file", "output_dir")}.items()),
        OpaqueFunction(function=start),
    ])
