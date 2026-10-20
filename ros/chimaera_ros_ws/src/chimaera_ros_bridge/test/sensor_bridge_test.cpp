#include <unistd.h>

#include <chimaera_ros_bridge/bridge.hpp>
#include <chrono>
#include <deque>
#include <fstream>
#include <iostream>
#include <rosgraph_msgs/msg/clock.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>
#include <std_msgs/msg/int32.hpp>
#include <std_msgs/msg/string.hpp>
#include <thread>

#include "controller_protocol.hpp"

using namespace std::chrono_literals;
using chimaera_ros_bridge::Bridge;
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

struct QueueController : chimaera::DataController
{
  chimaera::control::OutgoingQueue outgoing;
  void submit(chimaera::Message data, std::string_view key = {}) override
  {
    outgoing.submit(std::move(data), key);
  }
  std::deque<chimaera::Message> incoming;
  std::optional<chimaera::Message> take() override
  {
    if (incoming.empty()) {
      return std::nullopt;
    }
    auto message = std::move(incoming.front());
    incoming.pop_front();
    return message;
  }
};

struct MemoryTransport : chimaera::Transport
{
  std::deque<chimaera::Message> frames;
  chimaera::SendResult send(std::span<const std::byte> bytes) override
  {
    frames.emplace_back(bytes.begin(), bytes.end());
    return {};
  }
  chimaera::ReceiveResult receive() override
  {
    if (frames.empty()) return {{}, chimaera::TransportError::disconnected, "empty stream"};
    auto bytes = std::move(frames.front());
    frames.pop_front();
    return {std::move(bytes), chimaera::TransportError::none, {}};
  }
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  const std::string temporary = "/tmp/chimaera-config-test-" + std::to_string(getpid()) + ".json";
  try {
    const auto config = chimaera_ros_bridge::load_config(TEST_CONFIG);
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
      rejects([&] { chimaera_ros_bridge::load_config(temporary); });
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
      rejects([&] { chimaera_ros_bridge::load_config(temporary); });
    }
    auto transient = valid;
    transient.replace(transient.find("volatile"), 8, "transient_local");
    write(transient);
    require(
      chimaera_ros_bridge::load_config(temporary)[0].qos.durability() ==
        rclcpp::DurabilityPolicy::TransientLocal,
      "transient local QoS mismatch");
    rclcpp::NodeOptions options;
    options.parameter_overrides(
      {rclcpp::Parameter("config_file", TEST_CONFIG),
       rclcpp::Parameter("max_serialized_bytes", 131072)});
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
    for (const auto & sample :
         std::vector<std::string>{"", "hello", "γειά σου", std::string(131072 - 9, 'x')}) {
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
          executor.spin_some();
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
      executor.spin_some();
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
    oversized.data.assign(131072, 'x');
    host_pub->publish(oversized);
    bool rejected = false;
    until([&] {
      try {
        executor.spin_some();
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
    // Queue reads are passive; even a full batch pumps only once per exchange.
    int pump_count = 0;
    host->pump = [&] { ++pump_count; };
    require(host->take().has_value() && pump_count == 0, "take pumped callbacks");
    QueueController queued;
    host->exchange(queued);
    require(pump_count == 1 && host->pending() == 0, "batch drain pumped more than once");
    require(
      queued.outgoing.drain().size() == Bridge::max_messages - 1, "exchange lost outgoing frames");
    host->exchange(queued);
    require(pump_count == 2, "empty exchange did not pump once");
    host->pump = [&] { executor.spin_some(); };
    // Received data must be published before the single executor pump.
    QueueController incoming_controller;
    incoming_controller.incoming.push_back(saved);
    const auto received_before = guest->received();
    int delivery_pumps = 0;
    guest->pump = [&] {
      require(guest->received() == received_before + 1, "pump ran before incoming delivery");
      ++delivery_pumps;
    };
    guest->exchange(incoming_controller);
    require(
      delivery_pumps == 1 && incoming_controller.incoming.empty(),
      "receive exchange did not pump once");
    guest->pump = [&] { executor.spin_some(); };
    // Exercise the deployed routes with the largest supported benchmark scan.
    rclcpp::NodeOptions wall_options;
    wall_options.parameter_overrides(
      {rclcpp::Parameter("config_file", WALL_CONFIG),
       rclcpp::Parameter("max_serialized_bytes", 131072)});
    auto wall_host = std::make_shared<Bridge>("host", wall_options);
    wall_options.arguments(
      {"--ros-args", "-r", "/robot/scan:=/guest/scan", "-r", "/robot/cmd_vel:=/guest/cmd_vel"});
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
      executor.spin_some();
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

    // Sensor timestamps stay opaque to the bridge, and scans retain FIFO order.
    scan_history.clear();
    scan.ranges.assign(2, 1.25f);
    scan.intensities.assign(2, 3.5f);
    for (int seconds : {43, 200}) {
      scan.header.stamp.sec = seconds;
      scans->publish(scan);
      until([&] {
        executor.spin_some();
        return wall_host->pending() == (seconds == 43 ? 1u : 2u);
      });
    }
    QueueController controller;
    wall_host->exchange(controller);
    auto boundary = controller.outgoing.drain();
    require(boundary.size() == 2, "bridge dropped queued scans");
    for (auto & frame : boundary) {
      wall_guest->submit(std::move(frame));
    }
    until([&] {
      executor.spin_some();
      return scan_history.size() == 2;
    });
    require(
      scan_history[0].header.stamp.sec == 43 && scan_history[1].header.stamp.sec == 200,
      "scan timestamps or FIFO delivery changed");
    // Ball catching uses full RGB frames: exercise the same generic bridge and
    // bounded controller framing with both images in one boundary batch.
    write(R"({"version":1,"topics":[
      {"host_topic":"/test/stereo/left","guest_topic":"/test/guest/left",
       "type":"sensor_msgs/msg/Image","direction":"host_to_guest",
       "qos":{"history":"keep_last","depth":2,"reliability":"best_effort","durability":"volatile"}},
      {"host_topic":"/test/stereo/right","guest_topic":"/test/guest/right",
       "type":"sensor_msgs/msg/Image","direction":"host_to_guest",
       "qos":{"history":"keep_last","depth":2,"reliability":"best_effort","durability":"volatile"}},
      {"host_topic":"/test/clock","guest_topic":"/test/guest/clock",
       "type":"rosgraph_msgs/msg/Clock","direction":"host_to_guest","coalesce":true,
       "qos":{"history":"keep_last","depth":128,"reliability":"reliable","durability":"volatile"}}
    ]})");
    rclcpp::NodeOptions image_options;
    image_options.parameter_overrides(
      {rclcpp::Parameter("config_file", temporary),
       rclcpp::Parameter("max_serialized_bytes", 8 * 1024 * 1024),
       rclcpp::Parameter("max_pending_messages", 3)});
    auto image_host = std::make_shared<Bridge>("host", image_options);
    auto image_guest = std::make_shared<Bridge>("guest", image_options);
    executor.add_node(image_host);
    executor.add_node(image_guest);
    auto left =
      peer->create_publisher<sensor_msgs::msg::Image>("/test/stereo/left", rclcpp::SensorDataQoS());
    auto right = peer->create_publisher<sensor_msgs::msg::Image>(
      "/test/stereo/right", rclcpp::SensorDataQoS());
    std::vector<sensor_msgs::msg::Image> received_images;
    auto receive_image = [&](sensor_msgs::msg::Image::ConstSharedPtr message) {
      received_images.push_back(*message);
    };
    auto left_sub = peer->create_subscription<sensor_msgs::msg::Image>(
      "/test/guest/left", rclcpp::SensorDataQoS(), receive_image);
    auto right_sub = peer->create_subscription<sensor_msgs::msg::Image>(
      "/test/guest/right", rclcpp::SensorDataQoS(), receive_image);
    until([&] {
      return left->get_subscription_count() == 1 && right->get_subscription_count() == 1 &&
             left_sub->get_publisher_count() == 1 && right_sub->get_publisher_count() == 1;
    });
    for (const auto & dimensions : {std::pair{640u, 480u}, std::pair{1280u, 960u}}) {
      received_images.clear();
      sensor_msgs::msg::Image image;
      image.header.stamp.sec = 10;
      image.header.frame_id = "stereo";
      image.width = dimensions.first;
      image.height = dimensions.second;
      image.encoding = "rgb8";
      image.step = image.width * 3;
      image.data.resize(image.step * image.height);
      for (size_t i = 0; i < image.data.size(); ++i) image.data[i] = i % 251;
      left->publish(image);
      right->publish(image);
      until([&] {
        executor.spin_some();
        return image_host->pending() == 2;
      });
      QueueController image_controller;
      image_host->exchange(image_controller);
      auto batch = image_controller.outgoing.drain();
      require(batch.size() == 2, "stereo batch lost an image");
      MemoryTransport transport;
      chimaera::control::send_batch(transport, batch);
      auto decoded = chimaera::control::receive_batch(transport);
      for (auto & frame : decoded) image_guest->submit(std::move(frame));
      until([&] {
        executor.spin_some();
        return received_images.size() == 2;
      });
      require(
        received_images[0] == image && received_images[1] == image,
        "RGB image contents or timestamp changed");
      std::cout << "STEREO_TRANSFER_OK " << image.width << 'x' << image.height << '\n';
    }
    auto clock_pub = peer->create_publisher<rosgraph_msgs::msg::Clock>("/test/clock", 128);
    std::vector<rosgraph_msgs::msg::Clock> clocks;
    auto clock_sub = peer->create_subscription<rosgraph_msgs::msg::Clock>(
      "/test/guest/clock", 128,
      [&](rosgraph_msgs::msg::Clock::ConstSharedPtr message) { clocks.push_back(*message); });
    until([&] {
      return clock_pub->get_subscription_count() == 1 && clock_sub->get_publisher_count() == 1;
    });
    received_images.clear();
    sensor_msgs::msg::Image unchanged;
    unchanged.header.stamp.sec = 77;
    unchanged.width = 64;
    unchanged.height = 48;
    unchanged.step = 64 * 3;
    unchanged.encoding = "rgb8";
    unchanged.data.assign(unchanged.step * unchanged.height, 123);
    left->publish(unchanged);
    right->publish(unchanged);
    until([&] {
      executor.spin_some();
      return image_host->pending() == 2;
    });
    rosgraph_msgs::msg::Clock repeated;
    repeated.clock.sec = 1;
    for (int i = 0; i < 64; ++i) {
      clock_pub->publish(repeated);
      std::this_thread::sleep_for(2ms);
      executor.spin_some();
    }
    require(image_host->pending() == 3, "clock flood grew bridge queue or displaced images");
    QueueController clock_controller;
    image_host->exchange(clock_controller);
    // A new timestamp must replace the still-pending controller clock too.
    repeated.clock.sec = 2;
    clock_pub->publish(repeated);
    until([&] {
      executor.spin_some();
      return image_host->pending() == 1;
    });
    image_host->exchange(clock_controller);
    const auto clock_batch = clock_controller.outgoing.drain();
    require(
      clock_batch.size() == 3, "coalescing key did not reach controller or image FIFO changed");
    for (auto frame : clock_batch) image_guest->submit(std::move(frame));
    until([&] {
      executor.spin_some();
      return clocks.size() == 1 && received_images.size() == 2;
    });
    require(
      clocks[0] == repeated && received_images[0] == unchanged && received_images[1] == unchanged,
      "newest clock or unchanged image payloads were lost");
    std::cout << "CLOCK_COALESCING_OK: bridge/controller bounded; image FIFO retained\n";
    std::cout << "Config validation, bidirectional generic routing, QoS, frame/queue limits and "
                 "echo isolation, large scans and scan FIFO passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    status = 1;
  }
  std::remove(temporary.c_str());
  rclcpp::shutdown();
  return status;
}
