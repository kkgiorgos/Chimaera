#include <dlfcn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <chimaera_dds_transport/router.hpp>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/u_int64.hpp>
#include <thread>

using namespace std::chrono_literals;

int endpoint(const std::string & role, const std::string & directory)
{
  ::setenv("CHIMAERA_DDS_SOCKET", (directory + "/" + role).c_str(), 1);
  ::setenv("CHIMAERA_DDS_SIDE", role.c_str(), 1);
  ::setenv("CHIMAERA_DDS_ROUTES", TEST_ROUTES, 1);
  ::setenv("ROS_DOMAIN_ID", "217", 1);
  ::setenv("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp", 1);
  ::setenv("ROS_LOCALHOST_ONLY", "1", 1);
  ::setenv("ROS_LOG_DIR", directory.c_str(), 1);
  rclcpp::init(0, nullptr);
  auto node = std::make_shared<rclcpp::Node>("dds_transport_" + role);
  bool complete = false;
  bool graph_ready = false;
  unsigned next = 1, acknowledged = 0;
  auto qos = rclcpp::QoS(16).reliable();
  auto images = node->create_publisher<sensor_msgs::msg::Image>("/chimaera/source", qos);
  auto ack = node->create_publisher<std_msgs::msg::UInt64>("/ack", qos);
  auto image_sub = node->create_subscription<sensor_msgs::msg::Image>(
    "/images", qos, [&](sensor_msgs::msg::Image::ConstSharedPtr image) {
      if (role != "guest") return;
      if (
        image->header.stamp.sec != int(next) || image->data.size() != 921600 ||
        !std::all_of(
          image->data.begin(), image->data.end(), [&](uint8_t value) { return value == next; }))
        throw std::runtime_error("image corruption, duplicate or reordering");
      std_msgs::msg::UInt64 response;
      response.data = next++;
      ack->publish(response);
      if (next == 4) complete = true;
    });
  auto ack_sub = node->create_subscription<std_msgs::msg::UInt64>(
    "/chimaera/ack", qos,
    [&](std_msgs::msg::UInt64::ConstSharedPtr value) { acknowledged = value->data; });
  const auto deadline = std::chrono::steady_clock::now() + 30s;
  unsigned sent = 0;
  while (std::chrono::steady_clock::now() < deadline) {
    rclcpp::spin_some(node);
    const auto nodes = node->get_node_names();
    const auto remote = "/dds_transport_" + std::string(role == "host" ? "guest" : "host");
    const auto local_image = role == "host" ? "/chimaera/source" : "/images";
    graph_ready = std::find(nodes.begin(), nodes.end(), remote) != nodes.end() &&
                  node->count_publishers(local_image) && node->count_subscribers(local_image) &&
                  !node->get_publishers_info_by_topic(local_image).empty() &&
                  !node->get_subscriptions_info_by_topic(local_image).empty();
    if (
      role == "host" && sent < 3 && acknowledged == sent && graph_ready &&
      images->get_subscription_count() && ack_sub->get_publisher_count()) {
      sensor_msgs::msg::Image image;
      image.header.stamp.sec = ++sent;
      image.width = 640;
      image.height = 480;
      image.step = 1920;
      image.encoding = "rgb8";
      image.data.assign(921600, uint8_t(sent));
      images->publish(image);
    }
    if (role == "host" && acknowledged == 3) {
      complete = true;
      break;
    }
    // Keep the guest alive long enough for the reliable acknowledgement to drain.
    if (role == "guest" && complete) {
      std::this_thread::sleep_for(500ms);
      break;
    }
    std::this_thread::sleep_for(1ms);
  }
  rclcpp::shutdown();
  return complete && graph_ready ? 0 : 2;
}

int main(int argc, char ** argv)
{
  try {
    if (argc == 3) return endpoint(argv[1], argv[2]);
    const auto plugin = ::dlopen("librmw_fastrtps_cpp.so", RTLD_NOW | RTLD_LOCAL);
    if (!plugin || !::dlsym(plugin, "chimaera_rmw_transport_abi")) {
      std::fprintf(stderr, "Integration test requires the pinned Chimaera RMW overlay\n");
      if (plugin) ::dlclose(plugin);
      return 77;
    }
    ::dlclose(plugin);
    const auto directory =
      (std::filesystem::temp_directory_path() / ("chimaera-dds-test-" + std::to_string(::getpid())))
        .string();
    std::filesystem::create_directory(directory);
    auto spawn = [&](const char * role) {
      const auto pid = ::fork();
      if (pid < 0) throw std::runtime_error("fork failed");
      if (!pid) {
        ::execl(argv[0], argv[0], role, directory.c_str(), nullptr);
        ::_exit(127);
      }
      return pid;
    };
    // Fork before creating router threads; children initialize ROS only after exec.
    const auto host_pid = spawn("host"), guest_pid = spawn("guest");
    chimaera_dds::Router host(directory + "/host", 1), guest(directory + "/guest", 2);
    const auto deadline = std::chrono::steady_clock::now() + 35s;
    int host_status = -1, guest_status = -1;
    unsigned host_packets = 0, guest_packets = 0;
    std::size_t host_bytes = 0;
    while (std::chrono::steady_clock::now() < deadline) {
      while (auto packet = host.take()) {
        if (!host_packets) {
          auto file = std::fopen((directory + "/first-packet.bin").c_str(), "wb");
          if (file) {
            std::fwrite(packet->data(), 1, packet->size(), file);
            std::fclose(file);
          }
        }
        host_bytes += packet->size();
        guest.receive(packet->data(), packet->size());
        ++host_packets;
      }
      while (auto packet = guest.take()) {
        host.receive(packet->data(), packet->size());
        ++guest_packets;
      }
      if (host_status == -1) {
        int status;
        if (::waitpid(host_pid, &status, WNOHANG)) host_status = status;
      }
      if (guest_status == -1) {
        int status;
        if (::waitpid(guest_pid, &status, WNOHANG)) guest_status = status;
      }
      if (host_status != -1 && guest_status != -1) break;
      std::this_thread::sleep_for(1ms);
    }
    if (host_status == -1) {
      ::kill(host_pid, SIGKILL);
      ::waitpid(host_pid, &host_status, 0);
    }
    if (guest_status == -1) {
      ::kill(guest_pid, SIGKILL);
      ::waitpid(guest_pid, &guest_status, 0);
    }
    std::printf(
      "DDS packets: host=%u guest=%u; endpoint status: host=%d guest=%d\n", host_packets,
      guest_packets, host_status, guest_status);
    // All three unbounded images must have crossed the controlled packet path;
    // graph traffic alone cannot make a local DDS bypass pass this test.
    return host_status == 0 && guest_status == 0 && host_bytes >= 3 * 921600 && guest_packets ? 0
                                                                                              : 1;
  } catch (const std::exception & error) {
    std::fprintf(stderr, "%s\n", error.what());
    return 1;
  }
}
