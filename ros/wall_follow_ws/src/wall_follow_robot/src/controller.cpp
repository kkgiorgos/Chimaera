#include "wall_follow_robot/controller.hpp"

#include <iostream>

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int result = 0;
  try {
    auto node = std::make_shared<Controller>();
    rclcpp::spin(node);
    if (rclcpp::ok()) node->stop();
  } catch (const std::exception & e) {
    std::cerr << e.what() << '\n';
    result = 1;
  }
  rclcpp::shutdown();
  return result;
}
