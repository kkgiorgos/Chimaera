#include <algorithm>
#include <chimaera/gem5_controller.hpp>
#include <cmath>
#include <iomanip>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <wall_follow_bridge/bridge.hpp>
#include <wall_follow_bridge/status_bar.hpp>
#include <wall_follow_bridge/timing_controller.hpp>

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto node = std::make_shared<wall_follow_bridge::Bridge>("host");
    const auto interval = node->declare_parameter<int64_t>("interval_us", 100000);
    const auto poll = node->declare_parameter<int64_t>("poll_us", 10000);
    const auto steps = node->declare_parameter<int64_t>("steps", 0);
    const auto timeout = node->declare_parameter<int64_t>("startup_timeout_s", 300);
    const auto socket =
      node->declare_parameter<std::string>("timing_socket", "/tmp/chimaera_time.sock");
    if (
      interval < 1 || interval > 3600000000LL || poll < 1 || poll >= interval || steps < 0 ||
      timeout < 1) {
      throw std::invalid_argument(
        "require 0 < poll_us < interval_us <= 3600000000, "
        "steps >= 0 and startup_timeout_s > 0");
    }
    const auto report_seconds = node->declare_parameter<double>("report_seconds", 1.0);
    if (!std::isfinite(report_seconds) || report_seconds <= 0) {
      throw std::invalid_argument("report_seconds must be finite and positive");
    }
    wall_follow_bridge::StatusBar bar(node->declare_parameter<bool>("status_bar", true));
    std::ofstream timing_log;
    const auto timing_file = node->declare_parameter<std::string>("timing_file", "");
    if (!timing_file.empty()) {
      const auto parent = std::filesystem::path(timing_file).parent_path();
      if (!parent.empty()) { std::filesystem::create_directories(parent); }
      timing_log.open(timing_file);
      if (!timing_log) { throw std::runtime_error("Cannot open timing_file: " + timing_file); }
      timing_log << "step,sim_seconds,gem5_sim_seconds,gem5_wall_seconds,gazebo_wall_seconds,other_wall_seconds,pacing_wall_seconds,wall_seconds,elapsed_wall_seconds,startup_wall_seconds\n";
      timing_log << std::setprecision(17);
    }
    using Clock = std::chrono::steady_clock;
    auto last_report = Clock::time_point::min();
    auto refresh_due = [&] {
      const auto now = Clock::now();
      if (
        last_report != Clock::time_point::min() &&
        std::chrono::duration<double>(now - last_report).count() < report_seconds) {
        return false;
      }
      last_report = now;
      return true;
    };
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(node);
    node->pump = [&executor] { executor.spin_some(); };
    wall_follow_bridge::TimingController timing(
      socket, std::chrono::seconds(timeout), *node,
      node->declare_parameter<std::string>("gazebo_world", "wall_arena"),
      std::chrono::nanoseconds(node->declare_parameter<int64_t>("physics_step_ns", 1000000)),
      std::chrono::microseconds(poll));
    timing.pump = node->pump;
    timing.cancelled = [] { return !rclcpp::ok(); };
    auto & controller = timing;
    try {
      RCLCPP_INFO(node->get_logger(), "Host bridge initialized; waiting for gem5 guest");
      const auto waiting_since = Clock::now();
      timing.wait_until_ready(std::chrono::seconds(timeout), [&] {
        if (refresh_due()) {
          std::ostringstream text;
          text << " CHIMAERA | WAITING for gem5 | "
               << std::chrono::duration_cast<std::chrono::seconds>(Clock::now() - waiting_since)
                    .count()
               << "s / " << timeout << "s | " << socket;
          bar.update(text.str());
        }
        return !rclcpp::ok();
      });
      const double startup_seconds = std::chrono::duration<double>(Clock::now() - waiting_since).count();
      const auto run_begin = Clock::now();
      auto rate_report_time = run_begin;
      std::uint64_t rate_report_ticks = 0;
      RCLCPP_INFO(node->get_logger(), "Co-simulation runs without wall-clock pacing");
      std::uint64_t completed_steps = 0;
      auto report_status = [&] {
        if (!refresh_due()) {
          return;
        }
        const auto now = Clock::now();
        const auto ticks = timing.elapsed_ticks();
        const double sim_seconds = ticks / 1e12;
        const double wall_seconds = std::chrono::duration<double>(now - run_begin).count();
        const double recent_wall = std::chrono::duration<double>(now - rate_report_time).count();
        const double recent_rate = recent_wall > 0 ? (ticks - rate_report_ticks) / 1e12 / recent_wall : 0;
        const double average_rate = wall_seconds > 0 ? sim_seconds / wall_seconds : 0;
        rate_report_time = now;
        rate_report_ticks = ticks;
        std::ostringstream text;
        text << " CHIMAERA | " << (node->received() ? "ACTIVE" : "TIMING READY") << " | TX "
             << node->transmitted() << " RX " << node->received() << " Q " << node->pending()
             << " | " << std::fixed << std::setprecision(2) << recent_rate << "x (unpaced)"
             << " | sim " << sim_seconds << "s | step " << completed_steps;
        if (node->received()) {
          text << " | RX age "
               << std::chrono::duration<double>(Clock::now() - node->last_receive()).count() << "s";
        }
        text << " | ROS pub/sub " << node->local_talkers() << '/' << node->local_listeners()
             << " | avg " << average_rate << "x";
        if (completed_steps && timing.gem5_wall_seconds > 0 && timing.gazebo_wall_seconds > 0) {
          text << " | phase gem5 " << interval / 1e6 / timing.gem5_wall_seconds
               << "x Gazebo " << interval / 1e6 / timing.gazebo_wall_seconds << "x";
        }
        bar.update(text.str());
      };
      last_report = Clock::time_point::min();
      report_status();
      for (int64_t count = 0; rclcpp::ok() && (steps == 0 || count < steps); ++count) {
        const auto step_begin = Clock::now();
        const auto ticks_before = timing.elapsed_ticks();
        const auto result =
          controller.step(std::chrono::microseconds(interval));
        if (!result.ok()) {
          if (!rclcpp::ok()) { break; }
          throw std::runtime_error(result.message);
        }
        ++completed_steps;
        report_status();
        executor.spin_some();
        const auto end = Clock::now();
        const double active = std::chrono::duration<double>(end - step_begin).count();
        if (timing_log.is_open()) {
          timing_log << completed_steps << ',' << interval / 1e6 << ','
                     << (timing.elapsed_ticks() - ticks_before) / 1e12 << ','
                     << timing.gem5_wall_seconds << ',' << timing.gazebo_wall_seconds << ','
                     << std::max(0.0, active - timing.gem5_wall_seconds - timing.gazebo_wall_seconds) << ','
                     << 0.0 << ','
                     << std::chrono::duration<double>(end - step_begin).count() << ','
                     << std::chrono::duration<double>(end - run_begin).count() << ','
                     << startup_seconds << '\n';
          timing_log.flush();
          if (!timing_log) { throw std::runtime_error("Failed writing timing_file"); }
        }
      }
      const double sim_seconds = timing.elapsed_ticks() / 1e12;
      const double wall_seconds = std::chrono::duration<double>(Clock::now() - run_begin).count();
      bar.clear();
      RCLCPP_INFO(
        node->get_logger(), "Simulated %.3fs in %.3fs; average %.3fx", sim_seconds,
        wall_seconds, wall_seconds > 0 ? sim_seconds / wall_seconds : 0);
    } catch (...) {
      bar.clear();
      const auto result = controller.stop();
      if (result.state == chimaera::ControllerState::failed) {
        std::cerr << "Controller shutdown: " << result.message << '\n';
      }
      throw;
    }
    bar.update(" CHIMAERA | STOPPING gem5");
    const auto result = controller.stop();
    bar.clear();
    if (result.state == chimaera::ControllerState::failed) {
      throw std::runtime_error(result.message);
    }
    RCLCPP_INFO(
      node->get_logger(), "gem5 disconnected; TX %llu, RX %llu",
      static_cast<unsigned long long>(node->transmitted()),
      static_cast<unsigned long long>(node->received()));
    RCLCPP_INFO(
      node->get_logger(), "Clock callbacks %llu; superseded in bridge queue %llu "
      "(batch and transport queues also coalesce clocks)",
      static_cast<unsigned long long>(node->clock_updates_received()),
      static_cast<unsigned long long>(node->clock_updates_coalesced()));
  } catch (const std::exception & error) {
    std::cerr << "Host bridge: " << error.what() << '\n';
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
