// Small real ROS application for the checkpoint smoke test. Sequence state
// distinguishes a restored process from a fresh boot or a replayed response.
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/u_int64.hpp>

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("checkpoint_fixture");
  auto ready = node->create_publisher<std_msgs::msg::Bool>("/robot/ready", 1);
  auto camera = node->create_publisher<std_msgs::msg::Bool>("/robot/perception_ready", 1);
  auto response = node->create_publisher<std_msgs::msg::UInt64>("/smoke/response", 1);
  uint64_t previous = 0, sequence = 0;
  auto request = node->create_subscription<std_msgs::msg::UInt64>(
    "/smoke/request", 1, [&](std_msgs::msg::UInt64::ConstSharedPtr message) {
      if (message->data != previous) {
        previous = message->data;
        ++sequence;
      }
      std_msgs::msg::UInt64 reply;
      reply.data = (sequence << 32) | previous;
      response->publish(reply);
    });
  auto timer = node->create_wall_timer(std::chrono::milliseconds(1), [&] {
    std_msgs::msg::Bool message;
    message.data = previous != 0;
    ready->publish(message);
    camera->publish(message);
  });
  rclcpp::spin(node);
  rclcpp::shutdown();
}
