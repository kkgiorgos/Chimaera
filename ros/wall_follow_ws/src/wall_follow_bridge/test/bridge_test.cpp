#include <unistd.h>

#include <chrono>
#include <fstream>
#include <iostream>
#include <sensor_msgs/msg/laser_scan.hpp>
#include <std_msgs/msg/int32.hpp>
#include <std_msgs/msg/string.hpp>
#include <thread>
#include <wall_follow_bridge/bridge.hpp>

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
      message.data = "queued-" + std::to_string(i);
      host_pub->publish(message);
      until([&] {
        executor.spin_some();
        return host->pending() == i + 1;
      });
    }
    std_msgs::msg::String overflow;
    overflow.data = "newest";
    host_pub->publish(overflow);
    until([&] {
      executor.spin_some();
      return host->discarded() == 1;
    });
    require(host->pending() == Bridge::max_messages, "keep_last grew the queue");
    for (std::size_t i = 1; i <= Bridge::max_messages; ++i) {
      const auto count = at_guest.size();
      guest->submit(*host->take());
      until([&] { executor.spin_some(); return at_guest.size() > count; });
      require(
        at_guest.back() == (i == Bridge::max_messages ? "newest" : "queued-" + std::to_string(i)),
        "keep_last retained the wrong message");
    }
    overflow.data = "independent";
    host_pub->publish(overflow);
    until([&] { executor.spin_some(); return host->pending() == 1; });
    for (int i = 0; i < 20; ++i) {
      number.data = i;
      number_pub->publish(number);
      until([&] {
        executor.spin_some();
        return host->pending() == 1 + std::min(i + 1, 7) &&
               host->discarded() == 1 + std::max(0, i - 6);
      });
    }
    const auto strings_before = at_guest.size();
    const auto numbers_before = numbers.size();
    while (auto retained = host->take()) { guest->submit(std::move(*retained)); }
    until([&] {
      executor.spin_some();
      return at_guest.size() == strings_before + 1 && numbers.size() == numbers_before + 7;
    });
    require(at_guest.back() == "independent", "one route evicted another route");
    for (int i = 0; i < 7; ++i) {
      require(numbers[numbers_before + i] == 13 + i, "depth-7 retained stale messages");
    }
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
    auto scan_sub = peer->create_subscription<sensor_msgs::msg::LaserScan>(
      "/guest/scan", rclcpp::SensorDataQoS(),
      [&](sensor_msgs::msg::LaserScan::ConstSharedPtr message) { delivered = message; });
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
    std::cout << "Config validation, bidirectional generic routing, QoS, frame/queue limits and "
                 "echo isolation passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    status = 1;
  }
  std::remove(temporary.c_str());
  rclcpp::shutdown();
  return status;
}
