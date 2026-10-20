#include <unistd.h>

#include <chimaera_ros_bridge/bridge.hpp>
#include <chrono>
#include <fstream>
#include <iostream>
#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/int32.hpp>
#include <thread>

using namespace std::chrono_literals;
using chimaera_ros_bridge::Bridge;
void require(bool condition, const char * message)
{
  if (!condition) throw std::runtime_error(message);
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
  require(rejected, "invalid stereo input was accepted");
}
struct Controller : chimaera::DataController
{
  chimaera::control::OutgoingQueue outgoing;
  void submit(chimaera::Message data, std::string_view key = {}) override
  {
    outgoing.submit(std::move(data), key);
  }
  std::optional<chimaera::Message> take() override { return std::nullopt; }
};
int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  const std::string path = "/tmp/chimaera-stereo-test-" + std::to_string(getpid()) + ".json";
  try {
    const std::string config = R"({"version":1,"topics":[
      {"host_topic":"/fresh/host/left","guest_topic":"/fresh/guest/left",
       "type":"sensor_msgs/msg/Image","direction":"host_to_guest","coalesce":true,"coalesce_group":"stereo",
       "qos":{"history":"keep_last","depth":2,"reliability":"best_effort","durability":"volatile"}},
      {"host_topic":"/fresh/host/right","guest_topic":"/fresh/guest/right",
       "type":"sensor_msgs/msg/Image","direction":"host_to_guest","coalesce":true,"coalesce_group":"stereo",
       "qos":{"history":"keep_last","depth":2,"reliability":"best_effort","durability":"volatile"}},
      {"host_topic":"/fresh/host/control","guest_topic":"/fresh/guest/control",
       "type":"std_msgs/msg/Int32","direction":"host_to_guest",
       "qos":{"history":"keep_last","depth":8,"reliability":"reliable","durability":"volatile"}}
    ]})";
    auto write = [&](const std::string & text) { std::ofstream(path) << text; };
    write(config);
    require(chimaera_ros_bridge::load_config(path).size() == 3, "valid stereo config rejected");
    for (const auto & change : std::vector<std::pair<std::string, std::string>>{
           {"\"coalesce\":true", "\"coalesce\":false"},
           {"\"coalesce_group\":\"stereo\"", "\"coalesce_group\":\"other\""},
           {"sensor_msgs/msg/Image", "std_msgs/msg/String"}}) {
      auto bad = config;
      bad.replace(bad.find(change.first), change.first.size(), change.second);
      write(bad);
      rejects([&] { chimaera_ros_bridge::load_config(path); });
    }
    write(config);
    // Lossless stereo keeps unmatched samples until their peer arrives and
    // never silently replaces complete pairs or crosses missing timestamps.
    chimaera_ros_bridge::StereoQueue lossless;
    lossless.add("pair", "left", false);
    lossless.add("pair", "right", false);
    for (int64_t stamp = 1; stamp <= 8; ++stamp)
      require(
        !lossless.submit("pair", "left", stamp, chimaera::Message(1, std::byte(stamp))),
        "unpaired lossless image was delivered");
    for (int64_t stamp = 1; stamp <= 8; ++stamp) {
      auto pair = lossless.submit("pair", "right", stamp, chimaera::Message(1, std::byte(stamp)));
      require(
        pair && (*pair)[0][0] == std::byte(stamp) && (*pair)[1][0] == std::byte(stamp),
        "lossless stereo sample was replaced");
    }
    require(lossless.pending() == 0, "completed lossless pair retained buffers");
    rejects([&] { lossless.submit("pair", "left", 8, {}); });
    lossless.submit("pair", "left", 9, {});
    lossless.submit("pair", "left", 10, {});
    rejects([&] { lossless.submit("pair", "right", 10, {}); });
    std::array<std::byte, 12> header{};
    header[7] = std::byte{1};
    header[11] = std::byte{99};
    require(
      chimaera_ros_bridge::image_stamp(header.data(), header.size()) == 1000000099,
      "big endian stamp");
    header[1] = std::byte{1};
    std::reverse(header.begin() + 4, header.begin() + 8);
    std::reverse(header.begin() + 8, header.end());
    require(
      chimaera_ros_bridge::image_stamp(header.data(), header.size()) == 1000000099,
      "little endian stamp");
    rejects([&] { chimaera_ros_bridge::image_stamp(header.data(), 11); });
    header[7] = std::byte{255};
    rejects([&] { chimaera_ros_bridge::image_stamp(header.data(), header.size()); });

    rclcpp::NodeOptions options;
    options.parameter_overrides(
      {rclcpp::Parameter("config_file", path),
       rclcpp::Parameter("max_serialized_bytes", 8 * 1024 * 1024),
       rclcpp::Parameter("max_pending_messages", 3)});
    auto host = std::make_shared<Bridge>("host", options);
    constexpr int64_t budget = 22222223;
    options.append_parameter_override("max_image_age_ns", budget);
    options.append_parameter_override("image_diagnostics", true);
    auto guest = std::make_shared<Bridge>("guest", options);
    int64_t time = 0;
    bool available = true, expire_during_copy = false;
    int checks = 0;
    guest->simulation_time = [&]() -> std::optional<int64_t> {
      if (!available) return std::nullopt;
      return time + (expire_during_copy && checks++ > 0 ? budget : 0);
    };
    auto peer = std::make_shared<rclcpp::Node>("freshness_peer");
    auto left =
      peer->create_publisher<sensor_msgs::msg::Image>("/fresh/host/left", rclcpp::SensorDataQoS());
    auto right =
      peer->create_publisher<sensor_msgs::msg::Image>("/fresh/host/right", rclcpp::SensorDataQoS());
    auto control = peer->create_publisher<std_msgs::msg::Int32>("/fresh/host/control", 8);
    std::vector<sensor_msgs::msg::Image> images;
    std::vector<int> controls;
    auto receive = [&](sensor_msgs::msg::Image::ConstSharedPtr message) {
      images.push_back(*message);
    };
    auto left_sub = peer->create_subscription<sensor_msgs::msg::Image>(
      "/fresh/guest/left", rclcpp::SensorDataQoS(), receive);
    auto right_sub = peer->create_subscription<sensor_msgs::msg::Image>(
      "/fresh/guest/right", rclcpp::SensorDataQoS(), receive);
    auto control_sub = peer->create_subscription<std_msgs::msg::Int32>(
      "/fresh/guest/control", 8,
      [&](std_msgs::msg::Int32::ConstSharedPtr message) { controls.push_back(message->data); });
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(host);
    executor.add_node(guest);
    executor.add_node(peer);
    host->pump = [&] { executor.spin_some(); };
    auto until = [&](auto predicate) {
      const auto deadline = std::chrono::steady_clock::now() + 5s;
      while (!predicate()) {
        require(std::chrono::steady_clock::now() < deadline, "stereo discovery/delivery timed out");
        executor.spin_some();
        std::this_thread::sleep_for(2ms);
      }
    };
    until([&] {
      return left->get_subscription_count() == 1 && right->get_subscription_count() == 1 &&
             control->get_subscription_count() == 1 && left_sub->get_publisher_count() == 1 &&
             right_sub->get_publisher_count() == 1 && control_sub->get_publisher_count() == 1;
    });
    auto image = [](int64_t capture, uint32_t width = 64, uint32_t height = 48) {
      sensor_msgs::msg::Image value;
      value.header.stamp.sec = capture / 1000000000;
      value.header.stamp.nanosec = capture % 1000000000;
      value.header.frame_id = "stereo";
      value.width = width;
      value.height = height;
      value.step = width * 3;
      value.encoding = "rgb8";
      value.data.assign(value.step * value.height, capture % 251);
      return value;
    };
    left->publish(image(100000000));
    until([&] { return host->unmatched_images() == 1; });
    require(host->pending() == 0, "unpaired image was transmitted");
    right->publish(image(110000000));
    until([&] { return host->unmatched_images() == 2; });
    require(host->pending() == 0, "different timestamps formed a stereo pair");
    left->publish(image(110000000));
    until([&] { return host->pending() == 1; });
    for (int value : {5, 6}) {
      std_msgs::msg::Int32 command;
      command.data = value;
      control->publish(command);
      until([&] { return host->pending() == (value == 5 ? 2u : 3u); });
    }
    Controller controller;
    host->exchange(controller);
    left->publish(image(120000000));
    right->publish(image(120000000));
    until([&] { return host->pending() == 1; });
    host->exchange(controller);
    require(
      controller.outgoing.size() == 3, "pair coalescing lost controls or grew controller queue");
    time = 130000000;
    for (auto & wire : controller.outgoing.drain()) guest->submit(std::move(wire));
    until([&] { return images.size() == 2 && controls.size() == 2; });
    require(
      images[0] == image(120000000) && images[1] == image(120000000),
      "latest synchronized pair was not selected");
    require(controls == std::vector<int>({5, 6}), "control FIFO changed");

    auto packet = [&](int64_t capture, uint32_t width = 64, uint32_t height = 48) {
      left->publish(image(capture, width, height));
      right->publish(image(capture, width, height));
      until([&] { return host->pending() == 1; });
      host->exchange(controller);
      auto entry = controller.outgoing.take();
      require(
        entry.has_value() && !controller.outgoing.take(), "stereo replacement was not atomic");
      return std::move(entry->data);
    };
    int64_t capture = 200000000;
    for (auto dimensions : {std::pair{640u, 480u}, std::pair{1280u, 960u}}) {
      auto wire = packet(capture, dimensions.first, dimensions.second);
      const auto before = images.size();
      time = capture + budget;
      guest->submit(std::move(wire));
      until([&] { return images.size() == before + 2; });
      require(
        images.back() == image(capture, dimensions.first, dimensions.second),
        "paired RGB payload changed");
      capture += 100000000;
    }
    const auto before = guest->received();
    auto wire = packet(capture);
    time = capture + budget + 1;
    guest->submit(wire);
    time = capture - 1;
    guest->submit(wire);
    available = false;
    guest->submit(wire);
    available = true;
    time = capture + 1;
    expire_during_copy = true;
    checks = 0;
    guest->submit(wire);
    expire_during_copy = false;
    require(
      guest->received() == before && guest->stale_pairs() == 4,
      "expired/future/unknown-time images reached ROS");
    auto malformed = wire;
    malformed[4] = std::byte{255};
    rejects([&] { guest->submit(malformed); });
    auto number = [](const std::byte * data) {
      uint32_t value = 0;
      for (int i = 0; i < 4; ++i) value = (value << 8) | std::to_integer<uint32_t>(data[i]);
      return value;
    };
    const auto first = number(wire.data() + 4);
    const auto right_offset = 8 + first;
    const auto right_key = number(wire.data() + right_offset + 4);
    malformed = wire;
    malformed[right_offset + 8 + right_key + 8] ^= std::byte{1};
    rejects([&] { guest->submit(malformed); });
    for (int i = 1; i <= 10; ++i) {
      left->publish(image(capture + i * 10000000));
      executor.spin_some();
      std::this_thread::sleep_for(5ms);
      executor.spin_some();
    }
    require(
      host->unmatched_images() <= 3 && host->pending() == 0,
      "missing camera caused an unbounded queue");
    std::cout << "STEREO_FRESHNESS_OK: latest atomic pair, bounded unmatched images, expiry "
                 "before/after copies, control FIFO\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    status = 1;
  }
  std::remove(path.c_str());
  rclcpp::shutdown();
  return status;
}
