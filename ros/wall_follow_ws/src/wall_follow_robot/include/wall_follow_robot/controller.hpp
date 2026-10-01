#pragma once
#include <geometry_msgs/msg/twist.hpp>
#include <chrono>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>

#include "wall_follow_robot/parameters.hpp"

// Production workload: sensors, local scheduling, computation, and actuator commands.
class Controller : public rclcpp::Node
{
public:
  Controller() : Node("wall_follower")
  {
#define DECLARE(k) p_.k = declare_parameter(#k, p_.k);
    DOUBLE_PARAMS(DECLARE)
    INT_PARAMS(DECLARE)
#undef DECLARE
    p_.validate();
    pub_ = create_publisher<geometry_msgs::msg::Twist>("cmd_vel", 10);
    scan_sub_ = create_subscription<sensor_msgs::msg::LaserScan>(
      "scan", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::LaserScan::ConstSharedPtr s) {
        scan_ = s;
        scan_received_ = std::chrono::steady_clock::now();
      });
    reset_timer();
    callback_ =
      add_on_set_parameters_callback([this](const std::vector<rclcpp::Parameter> & values) {
        rcl_interfaces::msg::SetParametersResult result;
        auto candidate = p_;
        try {
          for (const auto & v : values) {
#define SET_DOUBLE(k)            \
  if (v.get_name() == #k) {      \
    candidate.k = v.as_double(); \
    continue;                    \
  }
#define SET_INT(k)            \
  if (v.get_name() == #k) {   \
    candidate.k = v.as_int(); \
    continue;                 \
  }
            DOUBLE_PARAMS(SET_DOUBLE)
            INT_PARAMS(SET_INT)
#undef SET_DOUBLE
#undef SET_INT
            throw std::invalid_argument("Only controller parameters are mutable during a run");
          }
          candidate.validate();
          bool changed = candidate.control_hz != p_.control_hz;
          p_ = candidate;
          if (changed) reset_timer();
          result.successful = true;
        } catch (const std::exception & e) {
          result.successful = false;
          result.reason = e.what();
        }
        return result;
      });
  }
  void stop() { pub_->publish(geometry_msgs::msg::Twist()); }

private:
  void reset_timer()
  {
    if (timer_) timer_->cancel();
    timer_ = create_wall_timer(
      std::chrono::nanoseconds(static_cast<int64_t>(1e9 / p_.control_hz)),
      [this] { step(); });
  }
  void step()
  {
    wall_follow::Command c;
    if (scan_) {
      // Host sensor stamps describe the Gazebo time domain. Freshness here is
      // measured from receipt using the robot computer's own monotonic clock.
      const double age = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - scan_received_).count();
      if (age >= 0 && age <= p_.scan_timeout)
        c = wall_follow::command(
          scan_->ranges, scan_->angle_min, scan_->angle_increment, scan_->range_min,
          scan_->range_max, p_);
      else
        c.state = "stale_scan";
    }
    geometry_msgs::msg::Twist msg;
    msg.linear.x = c.speed;
    msg.angular.z = c.yaw;
    pub_->publish(msg);
  }

private:
  Parameters p_;
  sensor_msgs::msg::LaserScan::ConstSharedPtr scan_;
  std::chrono::steady_clock::time_point scan_received_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr pub_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
  OnSetParametersCallbackHandle::SharedPtr callback_;
};
