#pragma once

#include <memory>
#include <rclcpp/rclcpp.hpp>

namespace ball_catching
{
std::shared_ptr<rclcpp::Node> make_perception();
std::shared_ptr<rclcpp::Node> make_intercept();
std::shared_ptr<rclcpp::Node> make_effort_control();
}  // namespace ball_catching
