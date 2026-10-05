#pragma once
#include "ball_catching_robot/core.hpp"
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/create_timer.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <json-c/json.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <algorithm>
#include <iostream>
#include <stdexcept>
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
class Recorder {
 public:
  Recorder(const std::string &directory, const std::string &name) {
    if (directory.empty()) return;
    std::filesystem::create_directories(directory);
    file_.open(std::filesystem::path(directory)/(name + ".jsonl"));
    if (!file_) throw std::runtime_error("Cannot open robot recording output");
  }
  void write(std::initializer_list<std::pair<std::string, Field>> fields) {
    if (!file_.is_open()) return;
    auto *record = json_object_new_object();
    for (const auto &[key, field] : fields) {
      json_object *value = nullptr;
      if (auto scalar = std::get_if<double>(&field)) value = json_object_new_double(*scalar);
      else if (auto text = std::get_if<std::string>(&field)) value = json_object_new_string(text->c_str());
      else if (auto array = std::get_if<std::vector<double>>(&field)) {
        value = json_object_new_array();
        for (double x : *array) json_object_array_add(value, json_object_new_double(x));
      }
      json_object_object_add(record, key.c_str(), value);
    }
    file_ << json_object_to_json_string_ext(record, JSON_C_TO_STRING_PLAIN) << '\n';
    file_.flush();
    json_object_put(record);
  }
 private:
  std::ofstream file_;
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
