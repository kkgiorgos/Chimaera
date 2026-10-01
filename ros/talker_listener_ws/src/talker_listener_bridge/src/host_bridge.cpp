#include <algorithm>
#include <chimaera/gem5_controller.hpp>
#include <chimaera/wall_clock_pacer.hpp>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <talker_listener_bridge/bridge.hpp>
#include <talker_listener_bridge/status_bar.hpp>
#include <thread>

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto node = std::make_shared<talker_listener_bridge::Bridge>("host");
    const auto interval = node->declare_parameter<int64_t>("interval_us", 100000);
    const auto poll = node->declare_parameter<int64_t>("poll_us", 10000);
    const auto steps = node->declare_parameter<int64_t>("steps", 0);
    const auto timeout = node->declare_parameter<int64_t>("startup_timeout_s", 300);
    const auto ratio = node->declare_parameter<double>("ratio", 1.0);
    const auto socket =
      node->declare_parameter<std::string>("timing_socket", "/tmp/chimaera_time.sock");
    if (
      interval < 1 || interval > 3600000000LL || poll < 1 || poll >= interval || steps < 0 ||
      timeout < 1 || !std::isfinite(ratio) || ratio <= 0) {
      throw std::invalid_argument(
        "require 0 < poll_us < interval_us <= 3600000000, "
        "steps >= 0, startup_timeout_s > 0 and finite ratio > 0");
    }
    const auto report_seconds = node->declare_parameter<double>("report_seconds", 1.0);
    if (!std::isfinite(report_seconds) || report_seconds <= 0) {
      throw std::invalid_argument("report_seconds must be finite and positive");
    }
    talker_listener_bridge::StatusBar bar(node->declare_parameter<bool>("status_bar", true));
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
    chimaera::Gem5TimingController timing(socket, std::chrono::seconds(timeout));
    chimaera::Gem5HostController controller(timing);
    try {
      RCLCPP_INFO(node->get_logger(), "Data transport ready; waiting for gem5 guest");
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
      chimaera::WallClockPacer pacer(ratio);
      std::uint64_t completed_steps = 0;
      auto report_status = [&] {
        if (!refresh_due()) {
          return;
        }
        const auto rate = pacer.report(timing.elapsed_ticks());
        std::ostringstream text;
        text << " CHIMAERA | " << (node->received() ? "ACTIVE" : "TIMING READY") << " | TX "
             << node->transmitted() << " RX " << node->received() << " Q " << node->pending()
             << " | " << std::fixed << std::setprecision(2) << rate.achieved_ratio << "x/" << ratio
             << "x"
             << " | sim " << rate.simulated_seconds << "s | step " << completed_steps;
        if (node->received()) {
          text << " | RX age "
               << std::chrono::duration<double>(Clock::now() - node->last_receive()).count() << "s";
        }
        text << " | ROS pub/sub " << node->local_talkers() << '/' << node->local_listeners()
             << " | avg " << rate.average_ratio << "x";
        bar.update(text.str());
      };
      last_report = Clock::time_point::min();
      report_status();
      for (int64_t count = 0; rclcpp::ok() && (steps == 0 || count < steps); ++count) {
        node->exchange(controller);
        const auto result =
          controller.step(std::chrono::microseconds(interval), std::chrono::microseconds(poll));
        if (!result.ok()) {
          throw std::runtime_error(result.message);
        }
        node->exchange(controller);
        ++completed_steps;
        while (rclcpp::ok()) {
          report_status();
          const auto delay = pacer.delay(timing.elapsed_ticks());
          if (delay <= chimaera::Duration::zero()) {
            break;
          }
          executor.spin_some();
          std::this_thread::sleep_for(
            std::min(delay, chimaera::Duration(std::chrono::milliseconds(10))));
        }
      }
      const auto report = pacer.report(timing.elapsed_ticks());
      bar.clear();
      RCLCPP_INFO(
        node->get_logger(), "Simulated %.3fs in %.3fs; average %.3fx", report.simulated_seconds,
        report.wall_seconds, report.average_ratio);
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
  } catch (const std::exception & error) {
    std::cerr << "Host bridge: " << error.what() << '\n';
    status = 1;
  }
  rclcpp::shutdown();
  return status;
}
