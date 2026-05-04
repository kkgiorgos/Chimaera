import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    IncludeLaunchDescription,
    OpaqueFunction,
    RegisterEventHandler,
    TimerAction,
)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

import xacro
import yaml


def load_yaml(package_name, relative_path):
    absolute_path = os.path.join(get_package_share_directory(package_name), relative_path)
    with open(absolute_path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def get_gazebo_robot_description(context, robot_type, load_gripper, franka_hand):
    robot_type_str = context.perform_substitution(robot_type)
    load_gripper_str = context.perform_substitution(load_gripper)
    franka_hand_str = context.perform_substitution(franka_hand)

    franka_xacro_file = os.path.join(
        get_package_share_directory("franka_description"),
        "robots",
        robot_type_str,
        robot_type_str + ".urdf.xacro",
    )

    robot_description_config = xacro.process_file(
        franka_xacro_file,
        mappings={
            "robot_type": robot_type_str,
            "hand": load_gripper_str,
            "ros2_control": "true",
            "gazebo": "true",
            "ee_id": franka_hand_str,
        },
    )

    robot_description = {"robot_description": robot_description_config.toxml()}
    use_sim_time = {"use_sim_time": True}

    return [
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="both",
            parameters=[robot_description, use_sim_time],
        )
    ]


def generate_launch_description():
    os.environ["GZ_SIM_RESOURCE_PATH"] = os.path.dirname(
        get_package_share_directory("franka_description")
    )

    demo_delay = LaunchConfiguration("demo_delay")
    gz_args = LaunchConfiguration("gz_args")
    load_gripper = LaunchConfiguration("load_gripper")
    franka_hand = LaunchConfiguration("franka_hand")
    robot_type = LaunchConfiguration("robot_type")
    execution_log_file = LaunchConfiguration("execution_log_file")
    point_to_point_repetitions = LaunchConfiguration("point_to_point_repetitions")
    cycle_pause_seconds = LaunchConfiguration("cycle_pause_seconds")
    use_sim_time = {"use_sim_time": True}
    controller_params = os.path.join(
        get_package_share_directory("franka_moveit_examples"),
        "config",
        "fr3_gazebo_trajectory_controller.yaml",
    )

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
    ompl_planning_pipeline_config = {
        "move_group": {
            "planning_plugin": "ompl_interface/OMPLPlanner",
            "request_adapters": "default_planner_request_adapters/AddTimeOptimalParameterization "
            "default_planner_request_adapters/ResolveConstraintFrames "
            "default_planner_request_adapters/FixWorkspaceBounds "
            "default_planner_request_adapters/FixStartStateBounds "
            "default_planner_request_adapters/FixStartStateCollision "
            "default_planner_request_adapters/FixStartStatePathConstraints",
            "start_state_max_bounds_error": 0.1,
        }
    }
    ompl_planning_pipeline_config["move_group"].update(
        load_yaml("franka_fr3_moveit_config", "config/ompl_planning.yaml")
    )

    moveit_controllers = {
        "moveit_simple_controller_manager": load_yaml(
            "franka_fr3_moveit_config", "config/fr3_controllers.yaml"
        ),
        "moveit_controller_manager": "moveit_simple_controller_manager/MoveItSimpleControllerManager",
    }
    trajectory_execution = {
        "moveit_manage_controllers": False,
        "trajectory_execution.allowed_execution_duration_scaling": 1.2,
        "trajectory_execution.allowed_goal_duration_margin": 0.5,
        "trajectory_execution.allowed_start_tolerance": 0.01,
    }
    planning_scene_monitor_parameters = {
        "publish_planning_scene": True,
        "publish_geometry_updates": True,
        "publish_state_updates": True,
        "publish_transforms_updates": True,
    }

    gazebo_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("ros_gz_sim"), "launch", "gz_sim.launch.py"])
        ),
        launch_arguments={"gz_args": gz_args}.items(),
    )

    robot_state_publisher = OpaqueFunction(
        function=get_gazebo_robot_description,
        args=[robot_type, load_gripper, franka_hand],
    )

    spawn_entity = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=["-topic", "/robot_description"],
        output="screen",
    )

    load_joint_state_broadcaster = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[
            "joint_state_broadcaster",
            "-t",
            "joint_state_broadcaster/JointStateBroadcaster",
            "--controller-manager-timeout",
            "60",
        ],
        output="screen",
    )

    load_fr3_arm_controller = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[
            "fr3_arm_controller",
            "-t",
            "joint_trajectory_controller/JointTrajectoryController",
            "-p",
            controller_params,
            "--controller-manager-timeout",
            "60",
        ],
        output="screen",
    )

    move_group_node = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[
            robot_description,
            robot_description_semantic,
            robot_description_kinematics,
            ompl_planning_pipeline_config,
            trajectory_execution,
            moveit_controllers,
            planning_scene_monitor_parameters,
            use_sim_time,
        ],
    )

    demo_node = Node(
        package="franka_moveit_examples",
        executable="point_to_point_demo",
        output="screen",
        parameters=[
            robot_description,
            robot_description_semantic,
            robot_description_kinematics,
            use_sim_time,
            {"execution_log_file": execution_log_file},
            {
                "point_to_point_repetitions": ParameterValue(
                    point_to_point_repetitions, value_type=int
                ),
                "cycle_pause_seconds": ParameterValue(
                    cycle_pause_seconds, value_type=float
                ),
            },
        ],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "demo_delay",
                default_value="12.0",
                description="Seconds to wait before starting the point-to-point demo node.",
            ),
            DeclareLaunchArgument(
                "gz_args",
                default_value="empty.sdf -r -s --headless-rendering",
                description="Arguments forwarded to Gazebo Sim.",
            ),
            DeclareLaunchArgument(
                "load_gripper",
                default_value="false",
                description="Whether to include the gripper in Gazebo.",
            ),
            DeclareLaunchArgument(
                "franka_hand",
                default_value="franka_hand",
                description="The gripper/end-effector id for Gazebo.",
            ),
            DeclareLaunchArgument(
                "robot_type",
                default_value="fr3",
                description="Robot model to spawn in Gazebo.",
            ),
            DeclareLaunchArgument(
                "execution_log_file",
                default_value="point_to_point_execution_times.csv",
                description="CSV file where point-to-point task durations are appended.",
            ),
            DeclareLaunchArgument(
                "point_to_point_repetitions",
                default_value="1",
                description=(
                    "Number of A-to-B runs. The demo returns to point A between repetitions."
                ),
            ),
            DeclareLaunchArgument(
                "cycle_pause_seconds",
                default_value="0.5",
                description="Seconds to wait between point-to-point motions.",
            ),
            gazebo_sim,
            robot_state_publisher,
            spawn_entity,
            RegisterEventHandler(
                OnProcessExit(
                    target_action=spawn_entity,
                    on_exit=[
                        TimerAction(period=5.0, actions=[load_joint_state_broadcaster]),
                        TimerAction(period=7.0, actions=[load_fr3_arm_controller]),
                    ],
                )
            ),
            move_group_node,
            TimerAction(period=demo_delay, actions=[demo_node]),
            RegisterEventHandler(
                OnProcessExit(
                    target_action=demo_node,
                    on_exit=[
                        EmitEvent(
                            event=Shutdown(
                                reason="point-to-point demo process exited"
                            )
                        )
                    ],
                )
            ),
        ]
    )
