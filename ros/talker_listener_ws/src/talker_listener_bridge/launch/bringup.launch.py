from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    share = Path(get_package_share_directory('chimaera_ros_bridge'))
    return LaunchDescription([
        DeclareLaunchArgument('manifest', description='Absolute path to the example session.json'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(share / 'launch/session.launch.py')),
                                 launch_arguments={'manifest': LaunchConfiguration('manifest')}.items()),
    ])
