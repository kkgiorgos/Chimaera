#include <atomic>
#include <chimaera_ros_bridge/completion_waitable.hpp>
#include <chrono>
#include <future>
#include <rclcpp/rclcpp.hpp>
#include <stdexcept>
#include <thread>

using namespace std::chrono_literals;
int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("completion_waitable_test");
  auto completion = std::make_shared<chimaera_ros_bridge::CompletionWaitable>();
  node->get_node_waitables_interface()->add_waitable(completion, nullptr);
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  executor.spin_some();
  // Notifications preceding wait must remain pending, including a burst.
  for (unsigned i = 0; i < 32; ++i) completion->notify();
  auto waiter = std::async(std::launch::async, [&] { executor.spin_once(-1ns); });
  if (waiter.wait_for(1s) != std::future_status::ready)
    throw std::runtime_error("DMA notification preceding wait was lost");
  waiter.get();
  for (unsigned i = 0; i < 64; ++i) {
    executor.spin_some();
    auto pending = std::async(std::launch::async, [&] { executor.spin_once(-1ns); });
    std::this_thread::sleep_for(1ms);
    completion->notify();
    if (pending.wait_for(1s) != std::future_status::ready)
      throw std::runtime_error("DMA notification did not wake blocked ROS executor");
    pending.get();
  }
  rclcpp::shutdown();
}
