#include <chrono>
#include <deque>
#include <fstream>
#include <iostream>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/int32.hpp>
#include <chimaera_ros_bridge/bridge.hpp>
#include <thread>
#include <unistd.h>

using namespace std::chrono_literals;
using chimaera_ros_bridge::Bridge;
void require(bool condition, const char * message)
{
  if (!condition) {throw std::runtime_error(message);}
}
template<typename F> void rejects(F operation)
{
  bool rejected = false;
  try {operation();} catch (const std::exception &) {rejected = true;}
  require(rejected, "invalid input was accepted");
}

struct QueueController : chimaera::DataController
{
  std::vector<chimaera::Message> outgoing;
  void submit(chimaera::Message data, std::string_view = {}) override
  {
    outgoing.push_back(std::move(data));
  }
  std::deque<chimaera::Message> incoming;
  std::optional<chimaera::Message> take() override
  {
    if (incoming.empty()) {return std::nullopt;}
    auto message = std::move(incoming.front());
    incoming.pop_front();
    return message;
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
    require(config[2].qos.depth() == 7 &&
      config[2].qos.reliability() == rclcpp::ReliabilityPolicy::BestEffort, "QoS mismatch");
    std::ifstream input(TEST_CONFIG);
    const std::string valid((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    auto write = [&](const std::string & text) {std::ofstream(temporary) << text;};
    for (const auto & bad : {std::string("{"), std::string("{}"), valid + "garbage"}) {
      write(bad);
      rejects([&] {chimaera_ros_bridge::load_config(temporary);});
    }
    for (const auto & change : std::vector<std::pair<std::string, std::string>>{
        {"keep_last", "keep_all"}, {"host_to_guest", "both"}, {"reliable", "system_default"},
        {"volatile", "persistent"}, {"128", "0"}, {"128", "129"}, {"128", "1.5"},
        {"history", "deadline"}, {"/test/host/route1", "/test/host/route0"},
        {"std_msgs/msg/String", ""}})
    {
      auto bad = valid;
      bad.replace(bad.find(change.first), change.first.size(), change.second);
      write(bad);
      rejects([&] {chimaera_ros_bridge::load_config(temporary);});
    }
    auto transient = valid;
    transient.replace(transient.find("volatile"), 8, "transient_local");
    write(transient);
    require(chimaera_ros_bridge::load_config(temporary)[0].qos.durability() ==
      rclcpp::DurabilityPolicy::TransientLocal, "transient local QoS mismatch");
    rclcpp::NodeOptions options;
    options.parameter_overrides({rclcpp::Parameter("config_file", TEST_CONFIG)});
    rejects([&] {Bridge missing("host");});
    auto invalid_limits = options;
    invalid_limits.append_parameter_override("max_serialized_bytes", int64_t(64 * 1024 * 1024));
    rejects([&] {Bridge invalid("host", invalid_limits);});
    auto host = std::make_shared<Bridge>("host", options);
    auto guest = std::make_shared<Bridge>("guest", options);
    require(!guest->local_routes_ready(), "guest was ready before its application existed");
    rejects([&] {guest->wait_for_local_routes(0s);});
    rejects([&] {guest->wait_for_local_routes(1s, [] {return true;});});
    auto remapped = options;
    remapped.arguments({"--ros-args", "-r", "/test/host/route1:=/test/host/route0"});
    rejects([&] {Bridge invalid("host", remapped);});
    auto peer = std::make_shared<rclcpp::Node>("bridge_test_peer");
    auto host_pub = peer->create_publisher<std_msgs::msg::String>("/test/host/route0", 128);
    auto guest_pub = peer->create_publisher<std_msgs::msg::String>("/test/guest/route1", 128);
    auto number_pub = peer->create_publisher<std_msgs::msg::Int32>(
      "/test/host/number", rclcpp::QoS(7).best_effort());
    std::vector<std::string> at_host, at_guest;
    std::vector<int> numbers;
    auto host_sub = peer->create_subscription<std_msgs::msg::String>(
      "/test/host/route1", 128,
      [&](std_msgs::msg::String::ConstSharedPtr m) {at_host.push_back(m->data);});
    auto guest_sub = peer->create_subscription<std_msgs::msg::String>(
      "/test/guest/route0", 128,
      [&](std_msgs::msg::String::ConstSharedPtr m) {at_guest.push_back(m->data);});
    // Two listeners on one route must not hide a missing listener on another.
    auto extra_sub = peer->create_subscription<std_msgs::msg::String>(
      "/test/guest/route0", 128, [](std_msgs::msg::String::ConstSharedPtr) {});
    const auto discovery_deadline = std::chrono::steady_clock::now() + 5s;
    while (guest->local_listeners() < 2 || guest->local_talkers() < 1) {
      require(std::chrono::steady_clock::now() < discovery_deadline, "partial discovery timed out");
      std::this_thread::sleep_for(5ms);
    }
    require(!guest->local_routes_ready(), "duplicate peers hid a missing application route");
    extra_sub.reset();
    auto number_sub = peer->create_subscription<std_msgs::msg::Int32>(
      "/test/guest/number", rclcpp::QoS(7).best_effort(),
      [&](std_msgs::msg::Int32::ConstSharedPtr m) {numbers.push_back(m->data);});
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(host); executor.add_node(guest); executor.add_node(peer);
    host->pump = guest->pump = [&] {executor.spin_some();};
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
    require(host->local_routes_ready() && guest->local_routes_ready(), "ready routes were rejected");
    guest->wait_for_local_routes(1s);
    chimaera::Message saved;
    for (const auto & sample : std::vector<std::string>{"", "hello", "γειά σου",
        std::string(Bridge::max_bytes - 9, 'x')})
    {
      for (bool forward : {true, false}) {
        auto & source = forward ? host : guest;
        auto & destination = forward ? guest : host;
        auto & received = forward ? at_guest : at_host;
        const auto count = received.size();
        std_msgs::msg::String message; message.data = sample;
        (forward ? host_pub : guest_pub)->publish(message);
        std::optional<chimaera::Message> wire;
        until([&] {executor.spin_some(); wire = source->take(); return wire.has_value();});
        rejects([&] {source->submit(*wire);});  // wrong direction
        if (forward) {saved = *wire;}
        destination->submit(std::move(*wire));
        until([&] {executor.spin_some(); return received.size() > count;});
        require(received.back() == sample, "String round trip mismatch");
        require(!host->take() && !guest->take(), "incoming publication echoed");
      }
    }
    std_msgs::msg::Int32 number; number.data = -314;
    number_pub->publish(number);
    std::optional<chimaera::Message> wire;
    until([&] {executor.spin_some(); wire = host->take(); return wire.has_value();});
    guest->submit(std::move(*wire));
    until([&] {executor.spin_some(); return !numbers.empty();});
    require(numbers.back() == -314, "generic Int32 routing failed");
    rejects([&] {guest->submit({});});
    auto bad = saved; bad[0] = std::byte{0};
    rejects([&] {guest->submit(bad);});
    bad = saved; bad[4] = std::byte{255};
    rejects([&] {guest->submit(bad);});
    bad = saved; bad[10] ^= std::byte{1};
    rejects([&] {guest->submit(bad);});
    bad = saved; bad.push_back(std::byte{0});
    rejects([&] {guest->submit(bad);});  // max serialized payload + 1
    bad = saved; bad.resize(8);
    rejects([&] {guest->submit(bad);});
    std_msgs::msg::String oversized; oversized.data.assign(Bridge::max_bytes, 'x');
    host_pub->publish(oversized);
    bool rejected = false;
    until([&] {
      try {executor.spin_some();} catch (const std::runtime_error &) {rejected = true;}
      return rejected;
    });
    // Fill the bridge queue deliberately without draining it.
    for (std::size_t i = 0; i < Bridge::max_messages; ++i) {
      std_msgs::msg::String message; message.data = "queued";
      host_pub->publish(message);
      until([&] {executor.spin_some(); return host->pending() == i + 1;});
    }
    std_msgs::msg::String overflow; host_pub->publish(overflow);
    rejected = false;
    until([&] {
      try {executor.spin_some();} catch (const std::runtime_error &) {rejected = true;}
      return rejected;
    });
    // Queue reads are passive; even a full batch pumps only once per exchange.
    int pump_count = 0;
    host->pump = [&] {++pump_count;};
    require(host->take().has_value() && pump_count == 0, "take pumped callbacks");
    QueueController queued;
    host->exchange(queued);
    require(pump_count == 1 && host->pending() == 0, "batch drain pumped more than once");
    require(queued.outgoing.size() == Bridge::max_messages - 1, "exchange lost outgoing frames");
    host->exchange(queued);
    require(pump_count == 2, "empty exchange did not pump once");
    host->pump = [&] {executor.spin_some();};
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
    require(delivery_pumps == 1 && incoming_controller.incoming.empty(), "receive exchange did not pump once");
    guest->pump = [&] {executor.spin_some();};
    // Larger application messages use configurable bounds without rebuilding.
    executor.remove_node(host); executor.remove_node(guest);
    host.reset(); guest.reset();
    auto large_options = options;
    large_options.append_parameter_override("max_serialized_bytes", int64_t(16384));
    large_options.append_parameter_override("max_pending_messages", int64_t(2));
    host = std::make_shared<Bridge>("host", large_options);
    guest = std::make_shared<Bridge>("guest", large_options);
    executor.add_node(host); executor.add_node(guest);
    until([&] {return host_pub->get_subscription_count() == 1 &&
        guest_sub->get_publisher_count() == 1;});
    std_msgs::msg::String large; large.data.assign(12000, 'L');
    const auto before_large = at_guest.size();
    host_pub->publish(large);
    until([&] {executor.spin_some(); wire = host->take(); return wire.has_value();});
    guest->submit(std::move(*wire));
    until([&] {executor.spin_some(); return at_guest.size() > before_large;});
    require(at_guest.back() == large.data, "configured large payload round trip failed");
    for (std::size_t i = 0; i < 2; ++i) {
      host_pub->publish(large);
      until([&] {executor.spin_some(); return host->pending() == i + 1;});
    }
    host_pub->publish(large);
    rejected = false;
    until([&] {
      try {executor.spin_some();} catch (const std::runtime_error &) {rejected = true;}
      return rejected;
    });
    std::cout << "Config validation, bidirectional generic routing, QoS, frame/queue limits and echo isolation passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n'; status = 1;
  }
  std::remove(temporary.c_str());
  rclcpp::shutdown();
  return status;
}
