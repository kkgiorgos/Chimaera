#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <chimaera/gem5_controller.hpp>
#include <iostream>
#include <talker_listener_bridge/bridge.hpp>

namespace
{
struct Mapping
{
  Mapping()
  {
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
  }
  ~Mapping() { unmap_m5_mem(); }
};
}  // namespace

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto node = std::make_shared<talker_listener_bridge::Bridge>("guest");
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(node);
    node->pump = [&executor] { executor.spin_some(); };
    Mapping mapping;
    chimaera::Gem5GuestController controller;
    RCLCPP_INFO(node->get_logger(), "Guest bridge ready; entering workbegin barrier");
    m5_work_begin_addr(0, 0);
    while (rclcpp::ok()) {
      const auto result = controller.run_next();
      if (result.state == chimaera::ControllerState::stopped) {
        break;
      }
      if (!result.ok()) {
        throw std::runtime_error(result.message);
      }
      // Deliver, pump ROS once, then queue outgoing messages for the next poll.
      node->exchange(controller);
    }
  } catch (const std::exception & error) {
    std::cerr << "Guest bridge: " << error.what() << '\n';
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
