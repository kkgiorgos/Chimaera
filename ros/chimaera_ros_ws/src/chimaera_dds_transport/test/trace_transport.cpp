#include <json-c/json.h>
#include <sys/wait.h>
#include <unistd.h>

#include <chimaera_dds_transport/router.hpp>
#include <chimaera_dds_transport/trace.hpp>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <set>
#include <thread>

#include "latest_sample.hpp"
#include "rtps_trace.hpp"

using namespace std::chrono_literals;
namespace fs = std::filesystem;
std::size_t payload_bytes()
{
  return std::getenv("CHIMAERA_TRACE_TEST_SMALL") ? 32 : 2 * 1024 * 1024;
}
void require(bool value, const char * reason)
{
  if (!value) throw std::runtime_error(reason);
}
int endpoint(const std::string & role, const fs::path & directory)
{
  const auto sockets = fs::path("/tmp") / ("chimaera-trace-hop-" + directory.filename().string());
  setenv("CHIMAERA_DDS_SOCKET", (sockets / role).c_str(), 1);
  setenv("CHIMAERA_DDS_SIDE", role.c_str(), 1);
  setenv("CHIMAERA_DDS_ROUTES", TEST_ROUTES, 1);
  setenv("CHIMAERA_TRACE", "1", 1);
  // Both native processes read the parent's logical clock. No gem5 clock DSO
  // is used by this test; role is known from each child's PID.
  setenv("CHIMAERA_TRACE_SIDE", "host", 1);
  setenv("CHIMAERA_TRACE_DIR", directory.c_str(), 1);
  setenv("CHIMAERA_TRACE_ROUTES", TEST_ROUTES, 1);
  setenv("CHIMAERA_TRACE_CLOCK_FILE", (directory / "clock").c_str(), 1);
  setenv("ROS_DOMAIN_ID", "218", 1);
  setenv("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp", 1);
  setenv("ROS_LOCALHOST_ONLY", "1", 1);
  setenv("ROS_LOG_DIR", directory.c_str(), 1);
  rclcpp::init(0, nullptr);
  auto node = std::make_shared<rclcpp::Node>("transport_trace_" + role);
  const auto deadline = std::chrono::steady_clock::now() + 20s;
  unsigned callbacks = 0;
  if (role == "guest") {
    unsigned second_callbacks = 0;
    auto subscription = node->create_subscription<sensor_msgs::msg::Image>(
      "/images", rclcpp::SensorDataQoS().keep_last(1),
      [&](sensor_msgs::msg::Image::ConstSharedPtr message) {
        require(
          message->data.size() == payload_bytes() && message->data.back() == 0xa5,
          "tracing corrupted the image payload");
        ++callbacks;
      });
    // Composed nodes share one DDS participant and transport locator. DDS must
    // fan one datagram out to both readers, without duplicate transport sends.
    auto second_subscription = node->create_subscription<sensor_msgs::msg::Image>(
      "/images", rclcpp::SensorDataQoS().keep_last(1),
      [&](sensor_msgs::msg::Image::ConstSharedPtr message) {
        require(
          message->data.size() == payload_bytes() && message->data.back() == 0xa5,
          "second reader received a corrupt payload");
        ++second_callbacks;
      });
    std::ofstream(directory / "guest-ready") << '\n';
    while (!fs::exists(directory / "release") && std::chrono::steady_clock::now() < deadline)
      std::this_thread::sleep_for(1ms);
    require(fs::exists(directory / "release"), "parent did not release the executor");
    while ((!callbacks || !second_callbacks) && std::chrono::steady_clock::now() < deadline) {
      rclcpp::spin_some(node);
      std::this_thread::sleep_for(1ms);
    }
    require(callbacks && second_callbacks, "DDS did not serve both readers of the shared locator");
    std::ofstream(directory / "done") << callbacks << '\n';
  } else {
    auto publisher = node->create_publisher<sensor_msgs::msg::Image>(
      "/chimaera/source", rclcpp::SensorDataQoS().keep_last(1));
    auto serialized = node->create_generic_publisher(
      "/chimaera/source", "sensor_msgs/msg/Image", rclcpp::SensorDataQoS().keep_last(1));
    while ((publisher->get_subscription_count() < 2 || serialized->get_subscription_count() < 2) &&
           std::chrono::steady_clock::now() < deadline)
      std::this_thread::sleep_for(1ms);
    require(
      publisher->get_subscription_count() == 2 && serialized->get_subscription_count() == 2,
      "trace test DDS discovery failed");
    sensor_msgs::msg::Image image;
    image.width = 1024;
    image.height = 2048;
    image.encoding = "mono8";
    image.step = 1024;
    image.data.assign(payload_bytes(), 0xa5);
    for (unsigned sequence = 1; sequence <= 2; ++sequence) {
      image.header.stamp.sec = sequence;
      publisher->publish(image);
    }
    image.header.stamp.sec = 3;
    rclcpp::SerializedMessage message;
    rclcpp::Serialization<sensor_msgs::msg::Image>().serialize_message(&image, &message);
    serialized->publish(message);
    std::ofstream(directory / "published") << '\n';
    while (!fs::exists(directory / "done") && std::chrono::steady_clock::now() < deadline)
      std::this_thread::sleep_for(1ms);
    require(fs::exists(directory / "done"), "receiver did not finish");
  }
  rclcpp::shutdown();
  return 0;
}
struct Observations
{
  struct Sample
  {
    std::set<unsigned> fragments;
    std::set<int64_t> times;
    unsigned total{};
    unsigned packets{};
  };
  std::map<std::string, Sample> samples;
  std::set<std::string> published, taken;
  unsigned packets{};
};
Observations read(const fs::path & directory, pid_t pid, const std::string & packet_event)
{
  Observations result;
  std::ifstream input(directory / ("chimaera-trace-host-" + std::to_string(pid) + ".jsonl"));
  for (std::string line; std::getline(input, line);) {
    auto * row = json_tokener_parse(line.c_str());
    if (!row) continue;  // The polling parent may observe a write in progress.
    auto get = [&](const char * name) { return json_object_object_get(row, name); };
    const std::string event = json_object_get_string(get("event"));
    const std::string id = json_object_get_string(get("id"));
    const auto time = json_object_get_int64(get("time_ns"));
    if (event == packet_event) {
      auto & sample = result.samples[id];
      sample.times.insert(time);
      const auto first = json_object_get_int(get("fragment_first"));
      const auto count = json_object_get_int(get("fragment_count"));
      sample.total = json_object_get_int(get("fragment_total"));
      sample.packets += count;
      for (int i = first; i < first + count; ++i) sample.fragments.insert(i);
      ++result.packets;
    } else if (event == "publish") {
      result.published.insert(id);
    } else if (event == "take") {
      require(time == 101000000, "DDS take did not include the imposed executor wait");
      result.taken.insert(id);
    }
    json_object_put(row);
  }
  return result;
}
int main(int argc, char ** argv)
{
  try {
    if (argc == 3) return endpoint(argv[1], argv[2]);
    const bool small = argc == 2;
    if (small) setenv("CHIMAERA_TRACE_TEST_SMALL", "1", 1);
    fs::create_directories(TEST_OUTPUT);
    auto pattern = (fs::path(TEST_OUTPUT) / "run-XXXXXX").string();
    require(mkdtemp(pattern.data()) != nullptr, "cannot create trace validation directory");
    const auto directory = fs::path(pattern);
    chimaera_dds::trace::create_clock((directory / "clock").string());
    chimaera_dds::trace::set_clock(1000000);
    auto spawn = [&](const char * role) {
      const auto pid = fork();
      require(pid >= 0, "fork failed");
      if (!pid) {
        execl(argv[0], argv[0], role, directory.c_str(), nullptr);
        _exit(127);
      }
      return pid;
    };
    const auto host = spawn("host"), guest = spawn("guest");
    const auto sockets = fs::path("/tmp") / ("chimaera-trace-hop-" + directory.filename().string());
    fs::create_directories(sockets);
    chimaera_dds::Router outgoing((sockets / "host").string(), 1);
    chimaera_dds::Router incoming((sockets / "guest").string(), 2);
    int host_status = -1, guest_status = -1;
    bool released = false;
    std::map<std::string, chimaera_dds::Packet> captured;
    const auto deadline = std::chrono::steady_clock::now() + 25s;
    while (std::chrono::steady_clock::now() < deadline) {
      while (auto packet = outgoing.take()) {
        if (small && packet->size() >= chimaera_dds::wire::header_bytes) {
          const auto parts = chimaera_dds::trace::parts(
            packet->data() + chimaera_dds::wire::header_bytes,
            packet->size() - chimaera_dds::wire::header_bytes);
          for (const auto & part : parts) captured[part.id] = *packet;
        }
        incoming.receive(std::move(*packet));
      }
      while (auto packet = incoming.take()) outgoing.receive(std::move(*packet));
      if (!released && fs::exists(directory / "published")) {
        const auto received = read(directory, guest, "receive");
        const auto sent = read(directory, host, "send");
        unsigned complete = 0;
        for (const auto & [id, sample] : received.samples)
          complete +=
            sent.published.count(id) && sample.total && sample.fragments.size() == sample.total;
        if (complete == 3) {
          require(received.taken.empty(), "executor consumed images before release");
          chimaera_dds::trace::set_clock(101000000);
          std::ofstream(directory / "release") << '\n';
          released = true;
        }
      }
      if (host_status == -1) {
        int status;
        if (waitpid(host, &status, WNOHANG)) host_status = status;
      }
      if (guest_status == -1) {
        int status;
        if (waitpid(guest, &status, WNOHANG)) guest_status = status;
      }
      if (host_status != -1 && guest_status != -1) break;
      std::this_thread::sleep_for(1ms);
    }
    for (auto [pid, status] : {std::pair{host, host_status}, std::pair{guest, guest_status}})
      if (status == -1) {
        kill(pid, SIGKILL);
        waitpid(pid, nullptr, 0);
      }
    require(released && host_status == 0 && guest_status == 0, "trace test endpoints failed");
    const auto sent = read(directory, host, "send"), received = read(directory, guest, "receive");
    require(sent.published.size() == 3, "typed/serialized source and transport identities differ");
    for (const auto & id : sent.published) {
      require(
        sent.samples.count(id) && received.samples.count(id) &&
          (small ? sent.samples.at(id).total == 1 : sent.samples.at(id).total > 1),
        "large image did not traverse the fragmented transport path");
      require(
        sent.samples.at(id).packets == sent.samples.at(id).total &&
          received.samples.at(id).packets == received.samples.at(id).total,
        "shared readers caused duplicate transport sends or deliveries");
      require(
        sent.samples.at(id).times == std::set<int64_t>{1000000} &&
          received.samples.at(id).times == std::set<int64_t>{1000000},
        "transport boundary was contaminated by executor waiting");
      if (small) {
        const auto & packet = captured.at(id);
        if (!chimaera_dds::latest_sample(packet.data(), packet.size())) {
          std::ofstream raw(directory / "unrecognized-data.bin", std::ios::binary);
          raw.write(reinterpret_cast<const char *>(packet.data()), packet.size());
          throw std::runtime_error("real small DDS DATA not coalescible: " + directory.string());
        }
      }
    }
    require(!received.taken.empty(), "no delayed RMW take recorded");
    std::cout << "3 typed/serialized fragmented images arrived before any executor take; "
              << "100 ms executor wait excluded from transport; traces: " << directory << '\n';
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
