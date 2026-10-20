#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <chimaera/dma_controller.hpp>
#include <chimaera/gem5_controller.hpp>
#include <chimaera_ros_bridge/bridge.hpp>
#include <chimaera_ros_bridge/checkpoint_barrier.hpp>
#include <chimaera_ros_bridge/completion_waitable.hpp>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <thread>

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
  std::unique_ptr<Mapping> mapping;
  try {
    auto node = std::make_shared<chimaera_ros_bridge::Bridge>("guest");
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(node);
    node->pump = [&] { node->drain_callbacks(executor); };
    const auto backend = node->declare_parameter<std::string>("transport_backend", "m5");
    const auto dma_diagnostics = node->declare_parameter<bool>("dma_diagnostics", false);
    if (backend != "m5" && backend != "dma")
      throw std::invalid_argument("transport_backend must be m5 or dma");
    std::shared_ptr<chimaera_ros_bridge::CompletionWaitable> completion;
    std::unique_ptr<chimaera::DmaGuestController> dma;
    if (backend == "dma") {
      if (std::getenv("CHIMAERA_LATENCY_REUSE"))
        throw std::invalid_argument("DMA currently requires a fresh guest boot");
      completion = std::make_shared<chimaera_ros_bridge::CompletionWaitable>();
      node->get_node_waitables_interface()->add_waitable(completion, nullptr);
      dma = std::make_unique<chimaera::DmaGuestController>(
        [completion] { completion->notify(); },
        node->declare_parameter<std::string>("dma_device", "/dev/chimaera-dma"),
        node->declare_parameter<std::string>("dma_module", ""),
        node->declare_parameter<std::string>("dma_module_options", ""));
      node->allocate = [&](std::size_t size) { return dma->allocate(size); };
    }
    const auto timeout = node->declare_parameter<int64_t>("startup_timeout_s", 300);
    if (timeout < 1) {
      throw std::invalid_argument("startup_timeout_s must be positive");
    }
    if (node->declare_parameter<bool>("wait_for_application", false)) {
      RCLCPP_INFO(
        node->get_logger(), "Waiting for guest application ROS endpoints before workbegin");
      node->wait_for_local_routes(std::chrono::seconds(timeout), [] { return !rclcpp::ok(); });
      const auto ready_file = node->declare_parameter<std::string>("application_ready_file", "");
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
      while (!ready_file.empty() && !std::filesystem::exists(ready_file)) {
        if (!rclcpp::ok() || std::chrono::steady_clock::now() >= deadline)
          throw std::runtime_error("Timed out waiting for application readiness: " + ready_file);
        executor.spin_some();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      RCLCPP_INFO(node->get_logger(), "Guest application ROS endpoints ready");
    }
    mapping = std::make_unique<Mapping>();
    const auto ops = node->declare_parameter<std::string>("m5ops", "address");
    const auto read_time = node->declare_parameter<bool>("simulation_time_from_readfile", false);
    bool checkpoint_control = node->declare_parameter<bool>("checkpoint_control", false);
    if (dma && checkpoint_control)
      throw std::invalid_argument("DMA checkpointing is not supported; use a fresh boot");
    if (ops != "address" && ops != "instruction") {
      throw std::invalid_argument("m5ops must be address or instruction");
    }
    chimaera::Gem5GuestController controller(
      ops == "instruction" ? chimaera::GuestM5Ops::instruction : chimaera::GuestM5Ops::address,
      ops == "instruction");
    RCLCPP_INFO(node->get_logger(), "Guest bridge ready; entering workbegin barrier");
    if (std::getenv("CHIMAERA_LATENCY_DETAILED_START"))
      m5_work_begin(0, 0);
    else
      m5_work_begin_addr(0, 0);
    bool time_ready = false;
    std::uint64_t iteration = 0;
    auto diagnostic = [&](const char * phase) {
      if (!dma || !dma_diagnostics) return;
      const auto data = std::string("phase=") + phase + " iteration=" + std::to_string(iteration) +
                        " " + dma->statistics() + "\n";
      m5_write_file_addr(
        const_cast<char *>(data.data()), data.size(), 0, "chimaera-dma-bridge-state.txt");
    };
    while (rclcpp::ok()) {
      ++iteration;
      diagnostic("before_service");
      const auto result =
        dma ? dma->run_next() : controller.run_next([&](chimaera::Duration idle_wait) {
          // Wait on DDS readiness so outgoing work wakes the bridge immediately.
          // The timeout remains the maximum interval between host-input polls.
          executor.spin_once(idle_wait);
          node->exchange(controller);
        });
      if (result.state == chimaera::ControllerState::stopped) {
        break;
      }
      if (!result.ok()) {
        throw std::runtime_error(result.message);
      }
      if (read_time && !time_ready && (!dma || dma->initialized())) {
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
      // Deliver incoming data and collect responses. Queued responses make the
      // next exchange immediate rather than sleeping for another poll interval.
      if (dma) {
        diagnostic("before_exchange");
        node->exchange(*dma);
        diagnostic("after_exchange");
        const auto sent = dma->run_next();
        if (!sent.ok()) throw std::runtime_error(sent.message);
        // Either DDS or a completion interrupt wakes the bridge. No periodic
        // host-input poll is needed. Guard notifications persist across races.
        if (!dma->has_work()) {
          diagnostic("before_wait");
          executor.spin_once(std::chrono::nanoseconds(-1));
          diagnostic("after_wait");
        }
      } else {
        node->exchange(controller);
      }
      if (std::getenv("CHIMAERA_LATENCY_REUSE")) {
        char control[96]{};
        const auto length =
          ops == "instruction" ? m5_read_file(control, 95, 0) : m5_read_file_addr(control, 95, 0);
        if (std::string(control, length) == "CHIMAERA_END_CASE\n") break;
      }
      if (checkpoint_control) {
        // Quiesce at a completed exchange so a checkpoint never splits a
        // transport header/payload or leaves a stale host handshake behind.
        char control[96]{};
        const auto length = m5_read_file_addr(control, sizeof(control) - 1, 0);
        if (std::string(control, length) == "CHIMAERA_CHECKPOINT\n") {
          checkpoint_control = false;
          controller.reconnect();
          m5_checkpoint_addr(0, 0);
          // An address-mode quiesce suspends the CPU inside a local memory
          // access. Its wakeup can race TimingSimpleCPU's outstanding fetch
          // response. Poll instead; draining stops the CPU at a safe boundary.
          chimaera_ros_bridge::wait_for_checkpoint_restore(
            [] {
              char data[96]{};
              const auto size = m5_read_file_addr(data, sizeof(data) - 1, 0);
              return std::string(data, size);
            },
            [] {}, [] { return rclcpp::ok(); });
        }
      }
    }
    RCLCPP_INFO(node->get_logger(), "Discarded stale stereo pairs: %lu", node->stale_pairs());
    if (dma) RCLCPP_INFO(node->get_logger(), "DMA buffers: %s", dma->statistics().c_str());
  } catch (const std::exception & error) {
    std::cerr << "Guest bridge: " << error.what() << '\n';
    if (mapping) {
      const std::string diagnostic = std::string(error.what()) + "\n";
      m5_write_file_addr(
        const_cast<char *>(diagnostic.data()), diagnostic.size(), 0,
        "chimaera-guest-bridge-error.txt");
    }
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
