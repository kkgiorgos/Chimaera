import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare
import yaml


def load_yaml(package_name, relative_path):
    absolute_path = os.path.join(get_package_share_directory(package_name), relative_path)
    with open(absolute_path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def generate_launch_description():
    demo_delay = LaunchConfiguration("demo_delay")

    franka_urdf_xacro = os.path.join(
        get_package_share_directory("franka_description"),
        "robots",
        "fr3",
        "fr3.urdf.xacro",
    )
    franka_srdf_xacro = os.path.join(
        get_package_share_directory("franka_description"),
        "robots",
        "fr3",
        "fr3.srdf.xacro",
    )

    robot_description = {
        "robot_description": ParameterValue(
            Command(
                [
                    FindExecutable(name="xacro"),
                    " ",
                    franka_urdf_xacro,
                    " hand:=false",
                    " robot_ip:=dont-care",
                    " ee_id:=none",
                    " use_fake_hardware:=true",
                    " fake_sensor_commands:=true",
                    " ros2_control:=true",
                ]
            ),
            value_type=str,
        )
    }
    robot_description_semantic = {
        "robot_description_semantic": ParameterValue(
            Command(
                [
                    FindExecutable(name="xacro"),
                    " ",
                    franka_srdf_xacro,
                    " hand:=false",
                    " ee_id:=none",
                ]
            ),
            value_type=str,
        )
    }
    robot_description_kinematics = {
        "robot_description_kinematics": load_yaml(
            "franka_fr3_moveit_config", "config/kinematics.yaml"
        )
    }

    moveit_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [FindPackageShare("franka_fr3_moveit_config"), "launch", "moveit.launch.py"]
            )
        ),
        launch_arguments={
            "robot_ip": "dont-care",
            "use_fake_hardware": "true",
            "fake_sensor_commands": "true",
            "load_gripper": "false",
            "ee_id": "none",
            "rviz": "false",
        }.items(),
    )

    demo_node = Node(
        package="franka_moveit_examples",
        executable="point_to_point_demo",
        output="screen",
        parameters=[robot_description, robot_description_semantic, robot_description_kinematics],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "demo_delay",
                default_value="5.0",
                description="Seconds to wait before starting the point-to-point demo node.",
            ),
            moveit_launch,
            TimerAction(period=demo_delay, actions=[demo_node]),
        ]
    )
