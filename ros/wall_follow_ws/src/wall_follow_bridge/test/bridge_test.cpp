#include <unistd.h>

#include <chrono>
#include <fstream>
#include <iostream>
#include <rosgraph_msgs/msg/clock.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>
#include <std_msgs/msg/int32.hpp>
#include <std_msgs/msg/string.hpp>
#include <thread>
#include <wall_follow_bridge/bridge.hpp>
#include "controller_protocol.hpp"

using namespace std::chrono_literals;
using wall_follow_bridge::Bridge;
void require(bool condition, const char * message)
{
  if (!condition) {
    throw std::runtime_error(message);
  }
}
template <typename F>
void rejects(F operation)
{
  bool rejected = false;
  try {
    operation();
  } catch (const std::exception &) {
    rejected = true;
  }
  require(rejected, "invalid input was accepted");
}

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  const std::string temporary = "/tmp/chimaera-config-test-" + std::to_string(getpid()) + ".json";
  try {
    const auto config = wall_follow_bridge::load_config(TEST_CONFIG);
    require(config.size() == 3, "route count mismatch");
    require(
      config[2].qos.depth() == 7 &&
        config[2].qos.reliability() == rclcpp::ReliabilityPolicy::BestEffort,
      "QoS mismatch");
    std::ifstream input(TEST_CONFIG);
    const std::string valid(
      (std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    auto write = [&](const std::string & text) { std::ofstream(temporary) << text; };
    for (const auto & bad : {std::string("{"), std::string("{}"), valid + "garbage"}) {
      write(bad);
      rejects([&] { wall_follow_bridge::load_config(temporary); });
    }
    for (const auto & change : std::vector<std::pair<std::string, std::string>>{
           {"keep_last", "keep_all"},
           {"host_to_guest", "both"},
           {"reliable", "system_default"},
           {"volatile", "persistent"},
           {"128", "0"},
           {"128", "129"},
           {"128", "1.5"},
           {"history", "deadline"},
           {"/test/host/route1", "/test/host/route0"},
           {"std_msgs/msg/String", ""}}) {
      auto bad = valid;
      bad.replace(bad.find(change.first), change.first.size(), change.second);
      write(bad);
      rejects([&] { wall_follow_bridge::load_config(temporary); });
    }
    auto transient = valid;
    transient.replace(transient.find("volatile"), 8, "transient_local");
    write(transient);
    require(
      wall_follow_bridge::load_config(temporary)[0].qos.durability() ==
        rclcpp::DurabilityPolicy::TransientLocal,
      "transient local QoS mismatch");
    rclcpp::NodeOptions options;
    options.parameter_overrides({rclcpp::Parameter("config_file", TEST_CONFIG)});
    auto host = std::make_shared<Bridge>("host", options);
    auto guest = std::make_shared<Bridge>("guest", options);
    auto remapped = options;
    remapped.arguments({"--ros-args", "-r", "/test/host/route1:=/test/host/route0"});
    rejects([&] { Bridge invalid("host", remapped); });
    auto peer = std::make_shared<rclcpp::Node>("bridge_test_peer");
    auto host_pub = peer->create_publisher<std_msgs::msg::String>("/test/host/route0", 128);
    auto guest_pub = peer->create_publisher<std_msgs::msg::String>("/test/guest/route1", 128);
    auto number_pub = peer->create_publisher<std_msgs::msg::Int32>(
      "/test/host/number", rclcpp::QoS(7).best_effort());
    std::vector<std::string> at_host, at_guest;
    std::vector<int> numbers;
    auto host_sub = peer->create_subscription<std_msgs::msg::String>(
      "/test/host/route1", 128,
      [&](std_msgs::msg::String::ConstSharedPtr m) { at_host.push_back(m->data); });
    auto guest_sub = peer->create_subscription<std_msgs::msg::String>(
      "/test/guest/route0", 128,
      [&](std_msgs::msg::String::ConstSharedPtr m) { at_guest.push_back(m->data); });
    auto number_sub = peer->create_subscription<std_msgs::msg::Int32>(
      "/test/guest/number", rclcpp::QoS(7).best_effort(),
      [&](std_msgs::msg::Int32::ConstSharedPtr m) { numbers.push_back(m->data); });
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(host);
    executor.add_node(guest);
    executor.add_node(peer);
    host->pump = guest->pump = [&] { executor.spin_some(); };
    auto until = [&](auto predicate) {
      const auto deadline = std::chrono::steady_clock::now() + 5s;
      while (!predicate()) {
        require(std::chrono::steady_clock::now() < deadline, "ROS discovery/delivery timed out");
        std::this_thread::sleep_for(5ms);
      }
    };
    until([&] {
      return host_pub->get_subscription_count() == 1 && guest_pub->get_subscription_count() == 1 &&
             number_pub->get_subscription_count() == 1 && host_sub->get_publisher_count() == 1 &&
             guest_sub->get_publisher_count() == 1 && number_sub->get_publisher_count() == 1;
    });
    chimaera::Message saved;
    for (const auto & sample : std::vector<std::string>{
           "", "hello", "γειά σου", std::string(Bridge::max_bytes - 9, 'x')}) {
      for (bool forward : {true, false}) {
        auto & source = forward ? host : guest;
        auto & destination = forward ? guest : host;
        auto & received = forward ? at_guest : at_host;
        const auto count = received.size();
        std_msgs::msg::String message;
        message.data = sample;
        (forward ? host_pub : guest_pub)->publish(message);
        std::optional<chimaera::Message> wire;
        until([&] {
          wire = source->take();
          return wire.has_value();
        });
        rejects([&] { source->submit(*wire); });  // wrong direction
        if (forward) {
          saved = *wire;
        }
        destination->submit(std::move(*wire));
        until([&] {
          executor.spin_some();
          return received.size() > count;
        });
        require(received.back() == sample, "String round trip mismatch");
        require(!host->take() && !guest->take(), "incoming publication echoed");
      }
    }
    std_msgs::msg::Int32 number;
    number.data = -314;
    number_pub->publish(number);
    std::optional<chimaera::Message> wire;
    until([&] {
      wire = host->take();
      return wire.has_value();
    });
    guest->submit(std::move(*wire));
    until([&] {
      executor.spin_some();
      return !numbers.empty();
    });
    require(numbers.back() == -314, "generic Int32 routing failed");
    rejects([&] { guest->submit({}); });
    auto bad = saved;
    bad[0] = std::byte{0};
    rejects([&] { guest->submit(bad); });
    bad = saved;
    bad[4] = std::byte{255};
    rejects([&] { guest->submit(bad); });
    bad = saved;
    bad[10] ^= std::byte{1};
    rejects([&] { guest->submit(bad); });
    bad = saved;
    bad.push_back(std::byte{0});
    rejects([&] { guest->submit(bad); });  // max serialized payload + 1
    bad = saved;
    bad.resize(8);
    rejects([&] { guest->submit(bad); });
    std_msgs::msg::String oversized;
    oversized.data.assign(Bridge::max_bytes, 'x');
    host_pub->publish(oversized);
    bool rejected = false;
    until([&] {
      try {
        (void)host->take();
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      return rejected;
    });
    // Fill the bridge queue deliberately without draining it.
    for (std::size_t i = 0; i < Bridge::max_messages; ++i) {
      std_msgs::msg::String message;
      message.data = "queued";
      host_pub->publish(message);
      until([&] {
        executor.spin_some();
        return host->pending() == i + 1;
      });
    }
    std_msgs::msg::String overflow;
    host_pub->publish(overflow);
    rejected = false;
    until([&] {
      try {
        executor.spin_some();
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      return rejected;
    });
    // Exercise the deployed routes with the largest supported benchmark scan.
    rclcpp::NodeOptions wall_options;
    wall_options.parameter_overrides({rclcpp::Parameter("config_file", WALL_CONFIG)});
    auto wall_host = std::make_shared<Bridge>("host", wall_options);
    wall_options.arguments(
      {"--ros-args", "-r", "/robot/scan:=/guest/scan", "-r", "/clock:=/guest/clock", "-r",
       "/robot/cmd_vel:=/guest/cmd_vel"});
    auto wall_guest = std::make_shared<Bridge>("guest", wall_options);
    executor.add_node(wall_host);
    executor.add_node(wall_guest);
    wall_host->pump = [&] { executor.spin_some(); };
    auto scans =
      peer->create_publisher<sensor_msgs::msg::LaserScan>("/robot/scan", rclcpp::SensorDataQoS());
    sensor_msgs::msg::LaserScan::ConstSharedPtr delivered;
    std::vector<sensor_msgs::msg::LaserScan> scan_history;
    auto scan_sub = peer->create_subscription<sensor_msgs::msg::LaserScan>(
      "/guest/scan", rclcpp::SensorDataQoS(),
      [&](sensor_msgs::msg::LaserScan::ConstSharedPtr message) {
        delivered = message;
        scan_history.push_back(*message);
      });
    until(
      [&] { return scans->get_subscription_count() == 1 && scan_sub->get_publisher_count() == 1; });
    sensor_msgs::msg::LaserScan scan;
    scan.header.frame_id = "robot/base/lidar";
    scan.header.stamp.sec = 42;
    scan.ranges.assign(8192, 1.25f);
    scan.intensities.assign(8192, 3.5f);
    scans->publish(scan);
    until([&] {
      wire = wall_host->take();
      return wire.has_value();
    });
    require(wire->size() > 4096, "scan did not exercise large payload routing");
    wall_guest->submit(std::move(*wire));
    until([&] {
      executor.spin_some();
      return bool(delivered);
    });
    require(*delivered == scan, "LaserScan round trip mismatch");

    // The host retains one latest clock even after a burst larger than its
    // queue capacity, without discarding or reordering surrounding scans.
    auto clocks = peer->create_publisher<rosgraph_msgs::msg::Clock>(
      "/clock", rclcpp::QoS(10).best_effort());
    std::vector<rosgraph_msgs::msg::Clock> clock_history;
    auto clock_sub = peer->create_subscription<rosgraph_msgs::msg::Clock>(
      "/guest/clock", rclcpp::QoS(10).best_effort(),
      [&](rosgraph_msgs::msg::Clock::ConstSharedPtr message) { clock_history.push_back(*message); });
    until([&] {
      return clocks->get_subscription_count() == 1 && clock_sub->get_publisher_count() == 1;
    });
    auto queue_clock = [&](int seconds) {
      const auto target = wall_host->clock_updates_received() + 1;
      rosgraph_msgs::msg::Clock message;
      message.clock.sec = seconds;
      message.clock.nanosec = 123456;
      clocks->publish(message);
      until([&] {
        executor.spin_some();
        return wall_host->clock_updates_received() == target;
      });
    };
    scan_history.clear();
    scan.ranges.assign(2, 1.25f);
    scan.intensities.assign(2, 3.5f);
    queue_clock(43);
    scan.header.stamp.sec = 43;
    scans->publish(scan);
    until([&] {
      executor.spin_some();
      return wall_host->pending() == 2;
    });
    for (int i = 0; i < static_cast<int>(Bridge::max_messages) + 16; ++i) {
      queue_clock(44 + i);
      require(wall_host->pending() == 2, "clock burst grew the bridge queue");
    }
    scan.header.stamp.sec = 200;
    scans->publish(scan);
    until([&] {
      executor.spin_some();
      return wall_host->pending() == 3;
    });
    auto boundary = chimaera::control::collect(*wall_host);
    require(boundary.size() == 3, "clock coalescing dropped scans");
    require(
      wall_host->coalescing_key(boundary[0]).empty() &&
        !wall_host->coalescing_key(boundary[1]).empty() &&
        wall_host->coalescing_key(boundary[2]).empty(),
      "clock coalescing changed scan order or latest clock position");
    require(wall_guest->coalescing_key(boundary[1]).empty(), "guest producer coalesces host routes");
    require(wall_host->coalescing_key({}).empty(), "invalid frame has a coalescing key");
    for (auto & frame : boundary) {
      wall_guest->submit(std::move(frame));
    }
    until([&] {
      executor.spin_some();
      return scan_history.size() == 2 && clock_history.size() == 1;
    });
    require(
      scan_history[0].header.stamp.sec == 43 && scan_history[1].header.stamp.sec == 200,
      "scan timestamps or FIFO delivery changed");
    require(
      clock_history[0].clock.sec == 187 && clock_history[0].clock.nanosec == 123456,
      "latest clock timestamp was modified or lost");
    require(wall_host->clock_updates_coalesced() == 144, "clock replacement count mismatch");

    // Replacing an existing clock must work when scans occupy every other slot.
    for (std::size_t i = 0; i < Bridge::max_messages - 1; ++i) {
      scans->publish(scan);
      until([&] {
        executor.spin_some();
        return wall_host->pending() == i + 1;
      });
    }
    queue_clock(201);
    require(wall_host->pending() == Bridge::max_messages, "queue did not fill");
    queue_clock(202);
    require(wall_host->pending() == Bridge::max_messages, "full queue clock replacement failed");
    boundary = chimaera::control::collect(*wall_host);
    require(
      boundary.size() == Bridge::max_messages &&
        !wall_host->coalescing_key(boundary.back()).empty(),
      "full queue replacement discarded scans");
    std::cout << "Config validation, bidirectional generic routing, QoS, frame/queue limits and "
                 "echo isolation, latest-clock coalescing and scan FIFO passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    status = 1;
  }
  std::remove(temporary.c_str());
  rclcpp::shutdown();
  return status;
}
