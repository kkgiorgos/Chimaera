#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <memory>
#include <sstream>
#include <stdexcept>
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

template <typename T>
T declare_or_get_parameter(
  const rclcpp::Node::SharedPtr& node, const std::string& name, const T& default_value)
{
  if (!node->has_parameter(name))
  {
    return node->declare_parameter<T>(name, default_value);
  }

  T value{};
  if (!node->get_parameter(name, value))
  {
    throw std::runtime_error("Failed to read parameter '" + name + "'");
  }
  return value;
}

std::string current_timestamp()
{
  const auto now = std::chrono::system_clock::now();
  const std::time_t now_time = std::chrono::system_clock::to_time_t(now);
  const std::tm local_time = *std::localtime(&now_time);

  std::ostringstream timestamp;
  timestamp << std::put_time(&local_time, "%Y-%m-%d %H:%M:%S");
  return timestamp.str();
}

void write_execution_log(
  std::ofstream& log_file, const std::string& label, const std::string& status,
  const double achieved_fraction, const double execution_duration_seconds, const int error_code)
{
  if (!log_file.is_open())
  {
    return;
  }

  log_file << current_timestamp() << ',' << label << ',' << status << ','
           << std::fixed << std::setprecision(6) << achieved_fraction << ','
           << execution_duration_seconds << ',' << error_code << '\n';
  log_file.flush();
}

bool compute_and_execute_cartesian_path(MoveGroupInterface& move_group,
                                        const geometry_msgs::msg::Pose& target,
                                        const std::string& label, const rclcpp::Logger& logger,
                                        std::ofstream& execution_log_file,
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
    write_execution_log(execution_log_file, label, "planning_failed", achieved_fraction, 0.0, -1);
    return false;
  }

  const auto execution_start = std::chrono::steady_clock::now();
  const auto execute_result = move_group.execute(trajectory);
  const auto execution_end = std::chrono::steady_clock::now();
  const std::chrono::duration<double> execution_duration = execution_end - execution_start;

  if (execute_result != moveit::core::MoveItErrorCode::SUCCESS)
  {
    RCLCPP_ERROR(
      logger,
      "Execution to %s failed after %.3f seconds with error code %d",
      label.c_str(), execution_duration.count(), execute_result.val);
    write_execution_log(
      execution_log_file, label, "execution_failed", achieved_fraction,
      execution_duration.count(), execute_result.val);
    return false;
  }

  RCLCPP_INFO(
    logger,
    "Reached %s with Cartesian fraction %.3f in %.3f seconds",
    label.c_str(), achieved_fraction, execution_duration.count());
  write_execution_log(
    execution_log_file, label, "succeeded", achieved_fraction, execution_duration.count(),
    0);
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
    declare_or_get_parameter<std::string>(node, "planning_group", "fr3_arm");
  const auto move_group_namespace =
    declare_or_get_parameter<std::string>(node, "move_group_namespace", "");
  const auto pose_reference_frame =
    declare_or_get_parameter<std::string>(node, "pose_reference_frame", "base");
  const auto point_a_y_offset =
    declare_or_get_parameter<double>(node, "point_a_y_offset", 0.10);
  const auto point_a_z_offset =
    declare_or_get_parameter<double>(node, "point_a_z_offset", 0.05);
  const auto point_b_y_delta =
    declare_or_get_parameter<double>(node, "point_b_y_delta", -0.20);
  const auto cartesian_eef_step =
    declare_or_get_parameter<double>(node, "cartesian_eef_step", 0.01);
  const auto cartesian_jump_threshold =
    declare_or_get_parameter<double>(node, "cartesian_jump_threshold", 0.0);
  const auto minimum_cartesian_fraction =
    declare_or_get_parameter<double>(node, "minimum_cartesian_fraction", 0.99);
  const auto max_velocity_scaling =
    declare_or_get_parameter<double>(node, "max_velocity_scaling", 0.2);
  const auto max_acceleration_scaling =
    declare_or_get_parameter<double>(node, "max_acceleration_scaling", 0.2);
  const auto execution_log_file_path =
    declare_or_get_parameter<std::string>(
      node, "execution_log_file", "point_to_point_execution_times.csv");

  std::ofstream execution_log_file;
  if (!execution_log_file_path.empty())
  {
    const bool write_header =
      !std::filesystem::exists(execution_log_file_path) ||
      std::filesystem::file_size(execution_log_file_path) == 0;

    execution_log_file.open(execution_log_file_path, std::ios::app);
    if (execution_log_file.is_open())
    {
      if (write_header)
      {
        execution_log_file
          << "timestamp,segment,status,cartesian_fraction,execution_duration_seconds,error_code\n";
      }
      RCLCPP_INFO(
        node->get_logger(), "Writing point-to-point execution times to %s",
        execution_log_file_path.c_str());
    }
    else
    {
      RCLCPP_WARN(
        node->get_logger(), "Failed to open execution log file %s",
        execution_log_file_path.c_str());
    }
  }

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
          move_group, point_a, "start_to_point_a", node->get_logger(), execution_log_file,
          cartesian_eef_step,
          cartesian_jump_threshold, minimum_cartesian_fraction))
    {
      exit_code = 1;
    }
    else
    {
      rclcpp::sleep_for(500ms);
      if (!compute_and_execute_cartesian_path(
            move_group, point_b, "point_a_to_point_b", node->get_logger(), execution_log_file,
            cartesian_eef_step,
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

  RCLCPP_INFO(node->get_logger(), "Point-to-point demo complete; shutting down.");

  executor.cancel();
  if (spinner.joinable())
  {
    spinner.join();
  }

  rclcpp::shutdown();
  return exit_code;
}
