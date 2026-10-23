#include <atomic>
#include <exception>
#include <filesystem>
#include <iostream>
#include <thread>

#include "ball_catching_robot/components.hpp"
#include "ball_catching_robot/runtime.hpp"

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int result = 0;
  try {
    auto control = ball_catching::make_effort_control();
    auto perception = ball_catching::make_perception();
    auto intercept = ball_catching::make_intercept();
    rclcpp::executors::SingleThreadedExecutor control_executor, data_executor;
    control_executor.add_node(control);
    data_executor.add_node(perception);
    data_executor.add_node(intercept);
    std::atomic<bool> freeze{false};
    rclcpp::TimerBase::SharedPtr checkpoint_timer;
    if (control->declare_parameter<bool>("checkpoint_control", false)) {
      checkpoint_timer = control->create_wall_timer(std::chrono::milliseconds(1), [&] {
        if (std::filesystem::exists("/tmp/chimaera-app-checkpoint-freeze")) {
          freeze = true;
          data_executor.cancel();
          control_executor.cancel();
        }
      });
    }
    while (rclcpp::ok()) {
      std::exception_ptr control_failure, data_failure;
      std::thread worker([&] {
        try { data_executor.spin(); }
        catch (...) { data_failure = std::current_exception(); rclcpp::shutdown(); }
      });
      try { control_executor.spin(); }
      catch (...) { control_failure = std::current_exception(); }
      if (!control_failure && rclcpp::ok() && freeze) {
        data_executor.cancel();
        worker.join();
        if (data_failure) std::rethrow_exception(data_failure);
        checkpoint_timer.reset(); // No polling survives checkpoint preparation.
        ball_catching::Recorder::checkpoint_watermarks("/tmp/chimaera-app-checkpoint-counts.json");
        while (rclcpp::ok() && std::filesystem::exists("/tmp/chimaera-app-checkpoint-freeze"))
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
        // The coordinator releases the application only after old peer disposal
        // callbacks returned. Wait for all replacement reliable consumers before
        // producing another record; real recorder backpressure remains explicit.
        while (rclcpp::ok() && !ball_catching::Recorder::subscribers_matched())
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
        while (rclcpp::ok() && (!std::filesystem::exists("/tmp/chimaera-app-checkpoint-resume") ||
                               !ball_catching::Recorder::subscribers_matched())) {
          ball_catching::Recorder::checkpoint_probe();
          std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        freeze = false;
        continue;
      }
      rclcpp::shutdown();
      data_executor.cancel();
      worker.join();
      if (control_failure) std::rethrow_exception(control_failure);
      if (data_failure) std::rethrow_exception(data_failure);
    }
  } catch (const std::exception & error) {
    std::cerr << "Composed robot: " << error.what() << '\n';
    result = 1;
  }
  rclcpp::shutdown();
  return result;
}
