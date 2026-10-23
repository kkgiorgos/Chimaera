#include <dlfcn.h>
#include <json-c/json.h>
#include <unistd.h>

#include <chimaera_dds_transport/trace.hpp>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <set>
#include <std_msgs/msg/string.hpp>
#include <stdexcept>
#include <thread>

int main(int argc, char ** argv)
{
  auto library = dlopen("librmw_fastrtps_cpp.so", RTLD_NOW | RTLD_LOCAL);
  if (!library || !dlsym(library, "chimaera_rmw_trace_abi")) return 77;
  const auto root =
    std::filesystem::temp_directory_path() / ("chimaera-trace-test-" + std::to_string(getpid()));
  std::filesystem::create_directory(root);
  const auto routes = root / "routes.json", clock = root / "clock";
  std::ofstream(routes)
    << R"({"topics":[{"host_topic":"/trace/test","guest_topic":"/trace/test"}]})";
  setenv("CHIMAERA_TRACE", "1", 1);
  setenv("CHIMAERA_TRACE_SIDE", "host", 1);
  setenv("CHIMAERA_TRACE_DIR", root.c_str(), 1);
  setenv("CHIMAERA_TRACE_ROUTES", routes.c_str(), 1);
  setenv("CHIMAERA_TRACE_CLOCK_FILE", clock.c_str(), 1);
  chimaera_dds::trace::create_clock(clock);
  chimaera_dds::trace::set_clock(1000000);
  if (
    !chimaera_dds::trace::enabled("/trace/test") ||
    chimaera_dds::trace::enabled("/trace/unselected"))
    throw std::runtime_error("route selection failed");
  chimaera_dds::trace::set_clock(500000);
  if (chimaera_dds::trace::now() != 1000000) throw std::runtime_error("clock rolled back");
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("chimaera_trace_test");
  unsigned received = 0;
  auto subscription = node->create_subscription<std_msgs::msg::String>(
    "/trace/test", 10, [&](std_msgs::msg::String::ConstSharedPtr message) {
      if (message->data == "unchanged payload") ++received;
    });
  auto publisher = node->create_publisher<std_msgs::msg::String>("/trace/test", 10);
  auto serialized_publisher =
    node->create_generic_publisher("/trace/test", "std_msgs/msg/String", 10);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  while (
    (!publisher->get_subscription_count() || !serialized_publisher->get_subscription_count()) &&
    std::chrono::steady_clock::now() < deadline) {
    rclcpp::spin_some(node);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  std_msgs::msg::String message;
  message.data = "unchanged payload";
  publisher->publish(message);
  rclcpp::SerializedMessage serialized;
  rclcpp::Serialization<std_msgs::msg::String> serialization;
  serialization.serialize_message(&message, &serialized);
  serialized_publisher->publish(serialized);
  chimaera_dds::trace::set_clock(7000000);
  while (received < 2 && std::chrono::steady_clock::now() < deadline) {
    rclcpp::spin_some(node);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  if (received != 2) throw std::runtime_error("missing typed or serialized DDS receipt");
  rclcpp::shutdown();
  std::ifstream input(root / ("chimaera-trace-host-" + std::to_string(getpid()) + ".jsonl"));
  std::set<std::string> publications, receipts;
  for (std::string line; std::getline(input, line);) {
    auto row = json_tokener_parse(line.c_str());
    if (!row) throw std::runtime_error("invalid JSON export");
    auto get = [&](const char * name) { return json_object_object_get(row, name); };
    const std::string event = json_object_get_string(get("event"));
    const std::string id = json_object_get_string(get("id"));
    if (event == "publish") {
      publications.insert(id);
      if (json_object_get_int64(get("time_ns")) != 1000000)
        throw std::runtime_error("publication clock mismatch");
    } else {
      receipts.insert(id);
      if (json_object_get_int64(get("time_ns")) != 7000000)
        throw std::runtime_error("take clock mismatch");
    }
    json_object_put(row);
  }
  if (publications.size() != 2 || publications != receipts)
    throw std::runtime_error("DDS publication/take identities do not match");
  std::filesystem::remove_all(root);
}
