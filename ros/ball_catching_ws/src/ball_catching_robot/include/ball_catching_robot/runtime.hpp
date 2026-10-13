#pragma once
#include "ball_catching_robot/core.hpp"
#include "ball_catching_robot/image_freshness.hpp"
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/create_timer.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <std_msgs/msg/string.hpp>
#include <json-c/json.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <memory>
#include <variant>

namespace ball_catching {
template<class Derived> std::vector<double> values(const Eigen::MatrixBase<Derived> &v) {
  return {v.derived().data(), v.derived().data() + v.size()};
}
inline Vec7 jointVector(const std::vector<double> &v) {
  if (v.size() != 7) throw std::invalid_argument("Expected seven joint values");
  const Vec7 result = Eigen::Map<const Vec7>(v.data());
  if (!result.allFinite()) throw std::invalid_argument("Nonfinite joint value");
  return result;
}
inline std::string readFile(const std::string &path) {
  std::ifstream file(path);
  if (!file) throw std::runtime_error("Cannot read " + path);
  return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
inline double seconds(const builtin_interfaces::msg::Time &t) { return t.sec + t.nanosec*1e-9; }
inline double seconds(const builtin_interfaces::msg::Duration &t) { return t.sec + t.nanosec*1e-9; }
using WallClock = std::chrono::steady_clock;
inline double elapsed(WallClock::time_point start) {
  return std::chrono::duration<double>(WallClock::now() - start).count();
}
using Field = std::variant<std::nullptr_t, double, std::string, std::vector<double>>;
template<class T> T recordingParameter(rclcpp::Node & node, const std::string & name, T value) {
  if (!node.has_parameter(name)) return node.declare_parameter<T>(name, value);
  return node.get_parameter(name).get_value<T>();
}
// ROS /clock can remain unchanged while a callback runs. Use m5_rpns in
// the guest to include modeled execution, scheduling and memory stalls.
class ProcessingClock {
 public:
  explicit ProcessingClock(rclcpp::Node & node)
    : clock_([] { return std::chrono::duration_cast<std::chrono::nanoseconds>(
          WallClock::now().time_since_epoch()).count(); },
        recordingParameter<std::string>(node, "gem5_clock_library", ""),
        recordingParameter<std::string>(node, "gem5_time_origin_file", "")) {}
  std::optional<int64_t> now() { return clock_.now(); }
  Field elapsed(std::optional<int64_t> start) {
    const auto end = now();
    if (!start || !end || *end < *start) return nullptr;
    return static_cast<double>(*end - *start) * 1e-9;
  }
 private:
  ImageClock clock_;
};
class Recorder {
 public:
  Recorder(const std::string &directory, const std::string &name, rclcpp::Node *node = nullptr)
    : name_(name) {
    if (node) {
      simulationTiming_ = !recordingParameter<std::string>(*node, "gem5_clock_library", "").empty();
      if (recordingParameter<bool>(*node, "record_telemetry", false)) {
        telemetry_ = node->create_publisher<std_msgs::msg::String>(
          "/benchmark/records", rclcpp::QoS(128).reliable());
        completionClock_ = std::make_unique<ImageClock>(
          [node] { return node->now().nanoseconds(); },
          node->get_parameter("gem5_clock_library").as_string(),
          recordingParameter<std::string>(*node, "gem5_time_origin_file", ""));
      }
    }
    if (directory.empty()) return;
    std::filesystem::create_directories(directory);
    file_.open(std::filesystem::path(directory)/(name + ".jsonl"));
    if (!file_) throw std::runtime_error("Cannot open robot recording output");
  }
  void write(std::initializer_list<std::pair<std::string, Field>> fields) {
    if (!file_.is_open() && !telemetry_) return;
    const auto completed = completionClock_ ? completionClock_->now() : std::nullopt;
    if (completionClock_ && !completed) return;  // Exclude pre-ROI startup.
    auto *record = json_object_new_object();
    for (const auto &[key, field] : fields) {
      json_object *value = nullptr;
      if (auto scalar = std::get_if<double>(&field)) value = json_object_new_double(*scalar);
      else if (auto text = std::get_if<std::string>(&field)) value = json_object_new_string(text->c_str());
      else if (auto array = std::get_if<std::vector<double>>(&field)) {
        value = json_object_new_array();
        for (double x : *array) json_object_array_add(value, json_object_new_double(x));
      }
      std::string outputKey = key;
      const std::string suffix = "_wall_seconds";
      if (simulationTiming_ && outputKey.size() >= suffix.size() &&
          outputKey.compare(outputKey.size() - suffix.size(), suffix.size(), suffix) == 0)
        outputKey.replace(outputKey.size() - suffix.size(), suffix.size(), "_simulation_seconds");
      json_object_object_add(record, outputKey.c_str(), value);
    }
    json_object_object_add(record, "timing_clock",
      json_object_new_string(simulationTiming_ ? "gem5 simulation" : "wall"));
    if (completed) json_object_object_add(record, "completed_time",
      json_object_new_double(static_cast<double>(*completed) * 1e-9));
    if (file_.is_open()) {
      file_ << json_object_to_json_string_ext(record, JSON_C_TO_STRING_PLAIN) << '\n';
      file_.flush();
    }
    if (telemetry_) {
      auto *envelope = json_object_new_object();
      json_object_object_add(envelope, "stream", json_object_new_string(name_.c_str()));
      json_object_object_add(envelope, "sequence", json_object_new_int64(sequence_++));
      json_object_object_add(envelope, "record", json_object_get(record));
      std_msgs::msg::String message;
      message.data = json_object_to_json_string_ext(envelope, JSON_C_TO_STRING_PLAIN);
      telemetry_->publish(message);
      json_object_put(envelope);
    }
    json_object_put(record);
  }
 private:
  std::ofstream file_;
  std::string name_;
  bool simulationTiming_{};
  uint64_t sequence_{};
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr telemetry_;
  std::unique_ptr<ImageClock> completionClock_;
};
struct Feedback {
  Vec7 q, dq;
  double time{-1};
  bool valid{false};
  void update(const sensor_msgs::msg::JointState &msg, const std::vector<std::string> &names) {
    Vec7 position, speed;
    for (size_t i = 0; i < names.size(); ++i) {
      const auto it = std::find(msg.name.begin(), msg.name.end(), names[i]);
      if (it == msg.name.end()) return;
      const auto j = static_cast<size_t>(it - msg.name.begin());
      if (j >= msg.position.size() || j >= msg.velocity.size()) return;
      position[i] = msg.position[j]; speed[i] = msg.velocity[j];
    }
    if (!position.allFinite() || !speed.allFinite()) return;
    q = position; dq = speed; time = seconds(msg.header.stamp); valid = true;
  }
};
// ROS-clock timers retain the simulation-time scheduling of the Python nodes.
inline rclcpp::TimerBase::SharedPtr timer(rclcpp::Node &node, double period, std::function<void()> callback) {
  return rclcpp::create_timer(node.get_node_base_interface(), node.get_node_timers_interface(),
      node.get_clock(), std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::duration<double>(period)),
      std::move(callback));
}
template<class Node> int run(int argc, char **argv) {
  rclcpp::init(argc, argv);
  int status = 0;
  try { rclcpp::spin(std::make_shared<Node>()); }
  catch (const std::exception &error) {
    if (rclcpp::ok()) { std::cerr << error.what() << '\n'; status = 1; }
  }
  rclcpp::shutdown();
  return status;
}
}
