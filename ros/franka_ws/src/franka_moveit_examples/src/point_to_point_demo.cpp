#include <chrono>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <geometry_msgs/msg/pose.hpp>
#include <moveit/move_group_interface/move_group_interface.h>
#include <moveit/utils/moveit_error_code.h>
#include <moveit_msgs/msg/robot_trajectory.hpp>
#include <rclcpp/rclcpp.hpp>

namespace
{
using moveit::planning_interface::MoveGroupInterface;
using namespace std::chrono_literals;

bool compute_and_execute_cartesian_path(MoveGroupInterface& move_group,
                                        const geometry_msgs::msg::Pose& target,
                                        const std::string& label, const rclcpp::Logger& logger,
                                        const double eef_step, const double jump_threshold,
                                        const double min_fraction)
{
  move_group.setStartStateToCurrentState();
  moveit_msgs::msg::RobotTrajectory trajectory;
  const std::vector<geometry_msgs::msg::Pose> waypoints{ target };
  const double achieved_fraction =
    move_group.computeCartesianPath(waypoints, eef_step, jump_threshold, trajectory, true);

  if (achieved_fraction < min_fraction)
  {
    RCLCPP_ERROR(
      logger,
      "Cartesian path to %s only achieved %.3f of the requested path (minimum %.3f)",
      label.c_str(), achieved_fraction, min_fraction);
    return false;
  }

  const auto execute_result = move_group.execute(trajectory);
  if (execute_result != moveit::core::MoveItErrorCode::SUCCESS)
  {
    RCLCPP_ERROR(logger, "Execution to %s failed with error code %d", label.c_str(), execute_result.val);
    return false;
  }

  RCLCPP_INFO(logger, "Reached %s with Cartesian fraction %.3f", label.c_str(), achieved_fraction);
  return true;
}
}  // namespace

int main(int argc, char* argv[])
{
  rclcpp::init(argc, argv);

  auto node = std::make_shared<rclcpp::Node>(
    "franka_point_to_point_demo",
    rclcpp::NodeOptions().automatically_declare_parameters_from_overrides(true));

  const auto planning_group =
    node->declare_parameter<std::string>("planning_group", "fr3_arm");
  const auto move_group_namespace =
    node->declare_parameter<std::string>("move_group_namespace", "");
  const auto pose_reference_frame =
    node->declare_parameter<std::string>("pose_reference_frame", "base");
  const auto point_a_y_offset =
    node->declare_parameter<double>("point_a_y_offset", 0.10);
  const auto point_a_z_offset =
    node->declare_parameter<double>("point_a_z_offset", 0.05);
  const auto point_b_y_delta =
    node->declare_parameter<double>("point_b_y_delta", -0.20);
  const auto cartesian_eef_step =
    node->declare_parameter<double>("cartesian_eef_step", 0.01);
  const auto cartesian_jump_threshold =
    node->declare_parameter<double>("cartesian_jump_threshold", 0.0);
  const auto minimum_cartesian_fraction =
    node->declare_parameter<double>("minimum_cartesian_fraction", 0.99);
  const auto max_velocity_scaling =
    node->declare_parameter<double>("max_velocity_scaling", 0.2);
  const auto max_acceleration_scaling =
    node->declare_parameter<double>("max_acceleration_scaling", 0.2);

  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  std::thread spinner([&executor]() { executor.spin(); });

  int exit_code = 0;

  try
  {
    MoveGroupInterface::Options options(planning_group, "robot_description", move_group_namespace);
    MoveGroupInterface move_group(node, options);

    move_group.startStateMonitor(5.0);
    if (!move_group.getCurrentState(5.0))
    {
      throw std::runtime_error("Timed out waiting for the current robot state");
    }

    move_group.setPlanningTime(10.0);
    move_group.setNumPlanningAttempts(5);
    move_group.allowReplanning(true);
    move_group.setMaxVelocityScalingFactor(max_velocity_scaling);
    move_group.setMaxAccelerationScalingFactor(max_acceleration_scaling);
    move_group.setGoalPositionTolerance(0.005);
    move_group.setGoalOrientationTolerance(0.02);
    move_group.setPoseReferenceFrame(pose_reference_frame);

    const auto current_pose = move_group.getCurrentPose().pose;
    geometry_msgs::msg::Pose point_a = current_pose;
    point_a.position.y += point_a_y_offset;
    point_a.position.z += point_a_z_offset;

    geometry_msgs::msg::Pose point_b = point_a;
    point_b.position.y += point_b_y_delta;

    RCLCPP_INFO(node->get_logger(), "Planning frame: %s", move_group.getPlanningFrame().c_str());
    RCLCPP_INFO(node->get_logger(), "End effector link: %s", move_group.getEndEffectorLink().c_str());
    RCLCPP_INFO(
      node->get_logger(),
      "Executing Cartesian point-to-point motion in group %s",
      planning_group.c_str());

    if (!compute_and_execute_cartesian_path(
          move_group, point_a, "point A", node->get_logger(), cartesian_eef_step,
          cartesian_jump_threshold, minimum_cartesian_fraction))
    {
      exit_code = 1;
    }
    else
    {
      rclcpp::sleep_for(500ms);
      if (!compute_and_execute_cartesian_path(
            move_group, point_b, "point B", node->get_logger(), cartesian_eef_step,
            cartesian_jump_threshold, minimum_cartesian_fraction))
      {
        exit_code = 1;
      }
    }
  }
  catch (const std::exception& exception)
  {
    RCLCPP_ERROR(node->get_logger(), "Point-to-point demo failed: %s", exception.what());
    exit_code = 1;
  }

  executor.cancel();
  if (spinner.joinable())
  {
    spinner.join();
  }

  rclcpp::shutdown();
  return exit_code;
}
