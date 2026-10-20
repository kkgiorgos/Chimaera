#include <algorithm>
#include <chimaera/gem5_controller.hpp>
#include <chimaera_ros_bridge/bridge.hpp>
#include <chimaera_ros_bridge/checkpoint_replay.hpp>
#include <chimaera_ros_bridge/gazebo_timing_controller.hpp>
#include <chimaera_ros_bridge/status_bar.hpp>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int status = 0;
  try {
    auto node = std::make_shared<chimaera_ros_bridge::Bridge>("host");
    const auto interval = node->declare_parameter<int64_t>("interval_us", 100000);
    const auto poll = node->declare_parameter<int64_t>("poll_us", 10000);
    const auto steps = node->declare_parameter<int64_t>("steps", 0);
    const auto timeout = node->declare_parameter<int64_t>("startup_timeout_s", 300);
    const auto socket =
      node->declare_parameter<std::string>("timing_socket", "/tmp/chimaera_time.sock");
    const auto capture_path = node->declare_parameter<std::string>("checkpoint_capture", "");
    const auto replay_path = node->declare_parameter<std::string>("checkpoint_replay", "");
    const auto checkpoint_directory =
      node->declare_parameter<std::string>("checkpoint_directory", "");
    const auto checkpoint_output = node->declare_parameter<std::string>("checkpoint_output", "");
    const auto pressure_warmup = node->declare_parameter<int64_t>("pressure_warmup_ns", 50000000);
    if (
      (!capture_path.empty() && !replay_path.empty()) || pressure_warmup < 0 ||
      pressure_warmup > 1000000000 ||
      ((!capture_path.empty() || !replay_path.empty()) && checkpoint_output.empty()))
      throw std::invalid_argument("Invalid checkpoint capture/replay or pressure warmup");
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
    chimaera_ros_bridge::StatusBar bar(node->declare_parameter<bool>("status_bar", true));
    std::ofstream timing_log;
    const auto timing_file = node->declare_parameter<std::string>("timing_file", "");
    if (!timing_file.empty()) {
      const auto parent = std::filesystem::path(timing_file).parent_path();
      if (!parent.empty()) {
        std::filesystem::create_directories(parent);
      }
      timing_log.open(timing_file);
      if (!timing_log) {
        throw std::runtime_error("Cannot open timing_file: " + timing_file);
      }
      timing_log << "step,sim_seconds,gem5_sim_seconds,gem5_wall_seconds,gazebo_wall_seconds,other_"
                    "wall_seconds,wall_seconds,elapsed_wall_seconds,startup_wall_seconds\n";
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
    node->pump = [&] { node->drain_callbacks(executor); };
    chimaera_ros_bridge::TimingController timing(
      socket, std::chrono::seconds(timeout), *node,
      node->declare_parameter<std::string>("gazebo_world", ""),
      std::chrono::nanoseconds(node->declare_parameter<int64_t>("physics_step_ns", 1000000)),
      std::chrono::microseconds(poll));
    timing.pump = node->pump;
    timing.cancelled = [] { return !rclcpp::ok(); };
    auto & controller = timing;
    std::unique_ptr<chimaera_ros_bridge::CheckpointReplay> capture;
    if (!capture_path.empty()) {
      capture = std::make_unique<chimaera_ros_bridge::CheckpointReplay>(capture_path, true);
      node->on_receive = [&](const chimaera::Message & frame) { capture->append(frame); };
    }
    try {
      const auto waiting_since = Clock::now();
      if (!replay_path.empty()) {
        timing.wait_for_gazebo(std::chrono::seconds(timeout), [] { return !rclcpp::ok(); });
        node->wait_for_local_routes(std::chrono::seconds(timeout), [] { return !rclcpp::ok(); });
        chimaera_ros_bridge::CheckpointReplay replay(replay_path, false);
        uint64_t replayed = 0;
        while (auto frames = replay.next()) {
          if (!rclcpp::ok()) throw std::runtime_error("Checkpoint replay cancelled");
          for (auto & frame : *frames) node->submit(std::move(frame));
          timing.replay_step(std::chrono::microseconds(interval));
          ++replayed;
        }
        // Fail closed if replay does not reproduce the saved robot boundary.
        std::ifstream expected(checkpoint_directory + "/checkpoint-host-state");
        std::ifstream actual(checkpoint_output + "/checkpoint-host-state");
        if (!std::filesystem::exists(checkpoint_output + "/checkpoint-ready"))
          throw std::runtime_error("Checkpoint replay did not restore arm/camera readiness");
        for (unsigned index = 0; index < 15; ++index) {
          double before, after;
          if (
            !(expected >> before) || !(actual >> after) || !std::isfinite(before) ||
            !std::isfinite(after) || std::abs(before - after) > (index == 0 ? 1e-9 : 1e-5))
            throw std::runtime_error(
              "Checkpoint host replay does not match saved time/joint state");
        }
        RCLCPP_INFO(
          node->get_logger(), "Replayed %lu host readiness intervals; gem5 stayed paused",
          replayed);
      }
      RCLCPP_INFO(node->get_logger(), "Host bridge initialized; waiting for gem5 guest");
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
      if (!replay_path.empty()) {
        // Draining advances the guest beyond the last recorded physics
        // boundary. Reconstruct that time before enabling memory pressure;
        // otherwise the warmup can expire while gem5 is still paused.
        std::istringstream restored(timing.command("BENCHMARK_RESTORE"));
        std::string marker, extra;
        uint64_t boundary, saved;
        if (
          !(restored >> marker >> boundary >> saved) || marker != "RESTORE" ||
          (restored >> extra) || saved < boundary)
          throw std::runtime_error("Invalid restored clock boundary");
        const auto interval_ticks = static_cast<uint64_t>(interval) * 1000000;
        const auto gap = saved - boundary;
        const auto catchup = gap / interval_ticks + (gap % interval_ticks != 0);
        for (uint64_t count = 0; count < catchup; ++count) {
          const auto result = controller.step(std::chrono::microseconds(interval));
          if (!result.ok()) throw std::runtime_error(result.message);
        }
        RCLCPP_INFO(node->get_logger(), "Aligned restored host clock in %lu intervals", catchup);
        if (timing.command("BENCHMARK_PRESSURE").rfind("PRESSURE ", 0) != 0)
          throw std::runtime_error("Simulator rejected pressure warmup");
        for (int64_t elapsed = 0; elapsed < pressure_warmup; elapsed += interval * 1000) {
          const auto result = controller.step(std::chrono::microseconds(interval));
          if (!result.ok()) throw std::runtime_error(result.message);
        }
        std::ofstream release(checkpoint_output + "/checkpoint-release");
        release << '\n';
        if (!release) throw std::runtime_error("Cannot release checkpoint launch gate");
      }
      const auto run_begin = Clock::now();
      const double startup_seconds =
        std::chrono::duration<double>(run_begin - waiting_since).count();
      const auto run_origin_ticks = timing.elapsed_ticks();
      auto rate_report_time = run_begin;
      std::uint64_t rate_report_ticks = run_origin_ticks;
      RCLCPP_INFO(node->get_logger(), "Co-simulation runs without wall-clock pacing");
      std::uint64_t completed_steps = 0;
      auto report_status = [&] {
        if (!refresh_due()) {
          return;
        }
        const auto now = Clock::now();
        const auto ticks = timing.elapsed_ticks();
        const double sim_seconds = (ticks - run_origin_ticks) / 1e12;
        const double wall_seconds = std::chrono::duration<double>(now - run_begin).count();
        const double recent_wall = std::chrono::duration<double>(now - rate_report_time).count();
        const double recent_rate =
          recent_wall > 0 ? (ticks - rate_report_ticks) / 1e12 / recent_wall : 0;
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
          text << " | phase gem5 " << interval / 1e6 / timing.gem5_wall_seconds << "x Gazebo "
               << interval / 1e6 / timing.gazebo_wall_seconds << "x";
        }
        bar.update(text.str());
      };
      last_report = Clock::time_point::min();
      report_status();
      for (int64_t count = 0; rclcpp::ok() && (steps == 0 || count < steps); ++count) {
        const auto step_begin = Clock::now();
        const auto ticks_before = timing.elapsed_ticks();
        const auto result = controller.step(std::chrono::microseconds(interval));
        if (!result.ok()) {
          if (!rclcpp::ok()) {
            break;
          }
          throw std::runtime_error(result.message);
        }
        ++completed_steps;
        if (capture) {
          capture->boundary();
          if (std::filesystem::exists(checkpoint_output + "/checkpoint-ready")) {
            capture->finish();
            node->on_receive = {};
            if (timing.command("BENCHMARK_CHECKPOINT").rfind("CHECKPOINT ", 0) != 0)
              throw std::runtime_error("Simulator rejected readiness checkpoint");
            break;
          }
        }
        report_status();
        executor.spin_some();
        const auto end = Clock::now();
        const double active = std::chrono::duration<double>(end - step_begin).count();
        if (timing_log.is_open()) {
          timing_log << completed_steps << ',' << interval / 1e6 << ','
                     << (timing.elapsed_ticks() - ticks_before) / 1e12 << ','
                     << timing.gem5_wall_seconds << ',' << timing.gazebo_wall_seconds << ','
                     << std::max(
                          0.0, active - timing.gem5_wall_seconds - timing.gazebo_wall_seconds)
                     << ',' << std::chrono::duration<double>(end - step_begin).count() << ','
                     << std::chrono::duration<double>(end - run_begin).count() << ','
                     << startup_seconds << '\n';
          timing_log.flush();
          if (!timing_log) {
            throw std::runtime_error("Failed writing timing_file");
          }
        }
      }
      const double sim_seconds = (timing.elapsed_ticks() - run_origin_ticks) / 1e12;
      const double wall_seconds = std::chrono::duration<double>(Clock::now() - run_begin).count();
      bar.clear();
      RCLCPP_INFO(
        node->get_logger(), "Simulated %.3fs in %.3fs; average %.3fx", sim_seconds, wall_seconds,
        wall_seconds > 0 ? sim_seconds / wall_seconds : 0);
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
