#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <chimaera/gem5_controller.hpp>
#include <chimaera_ros_bridge/bridge.hpp>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>

namespace
{
struct Mapping
{
  Mapping()
  {
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
  }
  ~Mapping() { unmap_m5_mem(); }
};
}  // namespace

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto node = std::make_shared<chimaera_ros_bridge::Bridge>("guest");
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(node);
    node->pump = [&executor] { executor.spin_some(); };
    const auto timeout = node->declare_parameter<int64_t>("startup_timeout_s", 300);
    if (timeout < 1) {
      throw std::invalid_argument("startup_timeout_s must be positive");
    }
    if (node->declare_parameter<bool>("wait_for_application", false)) {
      RCLCPP_INFO(
        node->get_logger(), "Waiting for guest application ROS endpoints before workbegin");
      node->wait_for_local_routes(std::chrono::seconds(timeout), [] { return !rclcpp::ok(); });
      RCLCPP_INFO(node->get_logger(), "Guest application ROS endpoints ready");
    }
    Mapping mapping;
    const auto ops = node->declare_parameter<std::string>("m5ops", "address");
    const auto read_time = node->declare_parameter<bool>("simulation_time_from_readfile", false);
    bool checkpoint_control = node->declare_parameter<bool>("checkpoint_control", false);
    if (ops != "address" && ops != "instruction") {
      throw std::invalid_argument("m5ops must be address or instruction");
    }
    chimaera::Gem5GuestController controller(
      ops == "instruction" ? chimaera::GuestM5Ops::instruction : chimaera::GuestM5Ops::address,
      ops == "instruction");
    RCLCPP_INFO(node->get_logger(), "Guest bridge ready; entering workbegin barrier");
    m5_work_begin_addr(0, 0);
    bool time_ready = false;
    while (rclcpp::ok()) {
      const auto result = controller.run_next();
      if (result.state == chimaera::ControllerState::stopped) {
        break;
      }
      if (!result.ok()) {
        throw std::runtime_error(result.message);
      }
      if (read_time && !time_ready) {
        // Bootstrap has established the host timing session. The experiment
        // replaces its consumed boot readfile with the fixed simulation origin.
        char data[96]{};
        const auto length =
          ops == "instruction" ? m5_read_file(data, 95, 0) : m5_read_file_addr(data, 95, 0);
        std::istringstream input(std::string(data, length));
        std::string marker, extra;
        uint64_t origin;
        if (
          !(input >> marker >> origin) || marker != "CHIMAERA_TIME_ORIGIN_NS" || (input >> extra)) {
          throw std::runtime_error("missing or invalid simulation time origin in readfile");
        }
        const std::string destination = "/tmp/chimaera-sim-time-origin.ns";
        {
          std::ofstream output(destination + ".tmp");
          output << origin << '\n';
          if (!output) throw std::runtime_error("cannot write simulation time origin");
        }
        if (std::rename((destination + ".tmp").c_str(), destination.c_str()) != 0) {
          throw std::runtime_error("cannot publish simulation time origin file");
        }
        node->simulation_time = [origin, ops]() -> std::optional<int64_t> {
          const auto current = ops == "instruction" ? m5_rpns() : m5_rpns_addr();
          if (current < origin || current - origin > INT64_MAX) return std::nullopt;
          return static_cast<int64_t>(current - origin);
        };
        time_ready = true;
        RCLCPP_INFO(node->get_logger(), "Authoritative simulation clock ready at %lu ns", origin);
      }
      // Deliver, pump ROS once, then queue outgoing messages for the next poll.
      node->exchange(controller);
      if (checkpoint_control) {
        // Quiesce at a completed exchange so a checkpoint never splits a
        // transport header/payload or leaves a stale host handshake behind.
        char control[96]{};
        const auto length = m5_read_file_addr(control, sizeof(control) - 1, 0);
        if (std::string(control, length) == "CHIMAERA_CHECKPOINT\n") {
          checkpoint_control = false;
          controller.reconnect();
          m5_checkpoint_addr(0, 0);
        }
      }
    }
    RCLCPP_INFO(node->get_logger(), "Discarded stale stereo pairs: %lu", node->stale_pairs());
  } catch (const std::exception & error) {
    std::cerr << "Guest bridge: " << error.what() << '\n';
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
