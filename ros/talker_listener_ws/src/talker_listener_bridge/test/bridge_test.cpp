#include <chrono>
#include <iostream>
#include <talker_listener_bridge/bridge.hpp>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

void require(bool condition, const char * message)
{
  if (!condition) {
    throw std::runtime_error(message);
  }
}

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto bridge = std::make_shared<talker_listener_bridge::Bridge>("host");
    auto peer = std::make_shared<rclcpp::Node>("bridge_test_peer");
    auto publisher = peer->create_publisher<std_msgs::msg::String>("/host/chatter", 128);
    std::vector<std::string> received;
    auto subscription = peer->create_subscription<std_msgs::msg::String>(
      "/guest/chatter", 128,
      [&](std_msgs::msg::String::ConstSharedPtr message) { received.push_back(message->data); });
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(bridge);
    executor.add_node(peer);
    bridge->pump = [&] { executor.spin_some(); };
    auto until = [&](auto predicate) {
      const auto deadline = std::chrono::steady_clock::now() + 5s;
      while (!predicate()) {
        require(std::chrono::steady_clock::now() < deadline, "ROS discovery/delivery timed out");
        std::this_thread::sleep_for(5ms);
      }
    };
    until([&] {
      return publisher->get_subscription_count() == 1 && subscription->get_publisher_count() == 1;
    });
    const std::vector<std::string> samples = {
      "", "hello from ROS", "γειά σου",
      std::string(talker_listener_bridge::Bridge::max_bytes, 'x')};
    for (const auto & sample : samples) {
      std_msgs::msg::String message;
      message.data = sample;
      publisher->publish(message);
      std::optional<chimaera::Message> wire;
      until([&] {
        wire = bridge->take();
        return wire.has_value();
      });
      require(wire->size() == sample.size(), "wire length mismatch");
      if (!sample.empty()) {
        require(std::memcmp(wire->data(), sample.data(), sample.size()) == 0, "wire data mismatch");
      }
      bridge->submit(std::move(*wire));
      const auto count = received.size();
      until([&] {
        executor.spin_some();
        return received.size() > count;
      });
      require(received.back() == sample, "incoming String mismatch");
      require(!bridge->take(), "incoming publication echoed into outgoing transport");
    }
    bool rejected = false;
    try {
      bridge->submit(chimaera::Message(talker_listener_bridge::Bridge::max_bytes + 1));
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    require(rejected, "oversized incoming payload accepted");
    std_msgs::msg::String oversized;
    oversized.data.assign(talker_listener_bridge::Bridge::max_bytes + 1, 'x');
    publisher->publish(oversized);
    rejected = false;
    until([&] {
      try {
        (void)bridge->take();
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      return rejected;
    });
    std::cout << "Bridge round trips, empty/UTF-8 strings, limits and echo isolation passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
