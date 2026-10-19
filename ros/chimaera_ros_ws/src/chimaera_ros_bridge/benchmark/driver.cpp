#include <ignition/msgs/boolean.pb.h>
#include <ignition/msgs/world_control.pb.h>
#include <ignition/msgs/world_stats.pb.h>

#include <chimaera_ros_bridge/gazebo_timing_controller.hpp>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <ignition/transport/Node.hh>
#include <iostream>
#include <mutex>
#include <rclcpp/rclcpp.hpp>
#include <sstream>
#include <thread>

#include "shared_clock.hpp"

namespace
{
using Clock = std::chrono::steady_clock;
void wait_file(const std::string & path, Clock::time_point deadline)
{
  while (!std::filesystem::exists(path)) {
    if (!rclcpp::ok() || Clock::now() >= deadline)
      throw std::runtime_error("Timed out or cancelled waiting for " + path);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
}

// Native control uses the same verified WorldControl multi_step protocol and
// 20 ms callback settling windows as the production Gazebo timing controller.
class NativeWorld
{
public:
  explicit NativeWorld(std::string world) : world_(std::move(world))
  {
    if (!node_.Subscribe("/world/" + world_ + "/stats", &NativeWorld::stats, this))
      throw std::runtime_error("Cannot subscribe to world statistics");
  }
  ~NativeWorld() { node_.Unsubscribe("/world/" + world_ + "/stats"); }
  void ready(Clock::time_point deadline)
  {
    std::unique_lock lock(mutex_);
    while (!seen_) {
      if (!rclcpp::ok() || Clock::now() >= deadline)
        throw std::runtime_error("Gazebo startup timed out");
      changed_.wait_for(lock, std::chrono::milliseconds(10));
    }
    if (!paused_ || time_ != 0) throw std::runtime_error("Gazebo must start paused at time zero");
  }
  void control(bool paused, uint32_t steps = 0)
  {
    ignition::msgs::WorldControl request;
    request.set_pause(paused);
    request.set_multi_step(steps);
    ignition::msgs::Boolean reply;
    bool accepted = false;
    if (
      !node_.Request("/world/" + world_ + "/control", request, 5000, reply, accepted) ||
      !accepted || !reply.data())
      throw std::runtime_error("Gazebo control request failed");
  }
  void step(uint64_t interval, uint64_t physics_step, Clock::time_point deadline)
  {
    uint64_t target, target_time;
    {
      std::lock_guard lock(mutex_);
      target = iterations_ + interval / physics_step;
      target_time = time_ + interval;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    control(true, interval / physics_step);
    std::unique_lock lock(mutex_);
    while (!(iterations_ >= target && paused_)) {
      if (!rclcpp::ok() || Clock::now() >= deadline)
        throw std::runtime_error("Gazebo step timed out");
      changed_.wait_for(lock, std::chrono::milliseconds(10));
    }
    if (iterations_ != target || time_ != target_time)
      throw std::runtime_error("Gazebo advanced outside the requested native step");
    lock.unlock();
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }

private:
  void stats(const ignition::msgs::WorldStatistics & stats)
  {
    std::lock_guard lock(mutex_);
    seen_ = true;
    iterations_ = stats.iterations();
    paused_ = stats.paused();
    time_ = stats.sim_time().sec() * 1000000000ULL + stats.sim_time().nsec();
    changed_.notify_all();
  }
  std::string world_;
  ignition::transport::Node node_;
  std::mutex mutex_;
  std::condition_variable changed_;
  bool seen_{}, paused_{};
  uint64_t iterations_{}, time_{};
};
}  // namespace

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto config = std::make_shared<rclcpp::Node>("latency_driver");
    const auto mode = config->declare_parameter<std::string>("mode", "native_free");
    const auto world = config->declare_parameter<std::string>("world", "latency_world");
    const auto interval = config->declare_parameter<int64_t>("interval_us", 50000);
    const auto poll = config->declare_parameter<int64_t>("poll_us", 10000);
    const auto physics_step = config->declare_parameter<int64_t>("physics_step_ns", 1000000);
    const auto timeout = config->declare_parameter<int64_t>("timeout_s", 300);
    const auto max_sim = config->declare_parameter<int64_t>("max_sim_ns", 15000000000);
    const auto done = config->declare_parameter<std::string>("done_file", "");
    const auto reuse_guest = config->declare_parameter<bool>("reuse_guest", false);
    const auto start_case = config->declare_parameter<bool>("start_case", false);
    if (
      (mode != "native_free" && mode != "native_stepped" && mode != "chimaera") || interval <= 0 ||
      interval > 3600000000LL || poll <= 0 || poll >= interval || physics_step <= 0 ||
      interval * 1000 % physics_step || timeout <= 0 || max_sim <= 0)
      throw std::invalid_argument("Invalid driver configuration");
    const auto deadline = Clock::now() + std::chrono::seconds(timeout);
    chimaera_latency::SharedClock physical(
      config->declare_parameter<std::string>("physics_clock_file", ""));
    if (mode == "chimaera") {
      auto bridge = std::make_shared<chimaera_ros_bridge::Bridge>(
        "host",
        rclcpp::NodeOptions().parameter_overrides(
          {rclcpp::Parameter("config_file", config->declare_parameter<std::string>("routes", "")),
           rclcpp::Parameter(
             "max_serialized_bytes",
             config->declare_parameter<int64_t>("max_serialized_bytes", 65536)),
           rclcpp::Parameter(
             "max_image_age_ns", config->declare_parameter<int64_t>("max_image_age_ns", 0))}));
      rclcpp::executors::SingleThreadedExecutor executor;
      executor.add_node(bridge);
      bridge->pump = [&executor] { executor.spin_some(); };
      chimaera_ros_bridge::TimingController timing(
        config->declare_parameter<std::string>("timing_socket", "/tmp/chimaera_time.sock"),
        std::chrono::seconds(timeout), *bridge, world, std::chrono::nanoseconds(physics_step),
        std::chrono::microseconds(poll));
      timing.pump = bridge->pump;
      timing.cancelled = [] { return !rclcpp::ok(); };
      chimaera_latency::SharedClock logical(
        config->declare_parameter<std::string>("clock_file", ""), true);
      uint64_t origin_fraction = 0;
      timing.on_gem5_boundary = [&logical, &origin_fraction](uint64_t ticks) {
        // m5_rpns truncates absolute ticks to nanoseconds. Preserve the origin's
        // fractional nanosecond so the two clocks agree even at exact boundaries.
        logical.set((ticks + origin_fraction) / 1000);
      };
      try {
        wait_file(config->declare_parameter<std::string>("host_ready", ""), deadline);
        bridge->wait_for_local_routes(std::chrono::seconds(timeout), [] { return !rclcpp::ok(); });
        if (start_case) {
          std::cout << "Latency startup: restarting guest session" << std::endl;
          if (timing.command("START_CASE").rfind("READY ", 0) != 0)
            throw std::runtime_error("Guest failed to reach the next workbegin barrier");
          std::cout << "Latency startup: next guest ready" << std::endl;
        }
        timing.wait_until_ready(std::chrono::seconds(timeout), [] { return !rclcpp::ok(); });
        std::istringstream origin(timing.command("STATUS"));
        std::string state;
        uint64_t initial_tick;
        if (!(origin >> state >> initial_tick) || state != "PAUSED")
          throw std::runtime_error("Missing authoritative clock origin");
        origin_fraction = initial_tick % 1000;
        logical.set(0);
        while (rclcpp::ok() && !std::filesystem::exists(done)) {
          if (Clock::now() >= deadline || timing.elapsed_ticks() / 1000 > uint64_t(max_sim))
            throw std::runtime_error("Benchmark timed out before complete guest export");
          const auto result = timing.step(std::chrono::microseconds(interval));
          if (!result.ok()) throw std::runtime_error(result.message);
        }
      } catch (...) {
        (void)timing.stop();
        throw;
      }
      if (reuse_guest) {
        if (timing.command("END_CASE") != "ENDING")
          throw std::runtime_error("Guest refused the case teardown barrier");
        while (timing.command("CASE_STATUS") != "IDLE") {
          if (!rclcpp::ok() || Clock::now() >= deadline)
            throw std::runtime_error("Guest case teardown timed out");
          const auto result = timing.step(std::chrono::microseconds(interval));
          if (!result.ok()) throw std::runtime_error(result.message);
        }
      } else {
        const auto result = timing.stop();
        if (result.state == chimaera::ControllerState::failed)
          throw std::runtime_error(result.message);
      }
    } else {
      NativeWorld gazebo(world);
      gazebo.ready(deadline);
      wait_file(config->declare_parameter<std::string>("host_ready", ""), deadline);
      wait_file(config->declare_parameter<std::string>("guest_ready", ""), deadline);
      if (mode == "native_free") gazebo.control(false);
      while (rclcpp::ok() && !std::filesystem::exists(done)) {
        if (
          Clock::now() >= deadline || (physical.now() != chimaera_latency::SharedClock::unset &&
                                       physical.now() > uint64_t(max_sim)))
          throw std::runtime_error("Native benchmark timed out");
        if (mode == "native_stepped")
          gazebo.step(interval * 1000, physics_step, deadline);
        else
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      gazebo.control(true);
    }
    if (!std::filesystem::exists(done)) throw std::runtime_error("Benchmark cancelled");
  } catch (const std::exception & error) {
    std::cerr << "Latency driver: " << error.what() << '\n';
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
