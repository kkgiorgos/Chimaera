#pragma once

#include <json-c/json.h>

#include <fstream>
#include <iterator>
#include <memory>
#include <rclcpp/rclcpp.hpp>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace wall_follow_bridge
{
struct Route
{
  std::string host_topic, guest_topic, type, direction, key;
  rclcpp::QoS qos{128};
};

namespace config_detail
{
inline void fields(json_object * object, std::initializer_list<const char *> allowed)
{
  if (!object || json_object_get_type(object) != json_type_object) {
    throw std::invalid_argument("configuration entry must be an object");
  }
  const std::set<std::string> names(allowed.begin(), allowed.end());
  json_object_object_foreach(object, name, value)
  {
    (void)value;
    if (!names.count(name)) {
      throw std::invalid_argument(std::string("unsupported configuration/QoS field: ") + name);
    }
  }
}
inline json_object * field(json_object * object, const char * name, json_type type)
{
  json_object * value = nullptr;
  if (
    !json_object_object_get_ex(object, name, &value) || !value ||
    json_object_get_type(value) != type) {
    throw std::invalid_argument(std::string("missing or invalid field: ") + name);
  }
  return value;
}
inline std::string string(json_object * object, const char * name)
{
  auto * value = field(object, name, json_type_string);
  std::string result(json_object_get_string(value), json_object_get_string_len(value));
  if (result.empty() || result.size() > 256 || result.find('\0') != std::string::npos) {
    throw std::invalid_argument(std::string("invalid string: ") + name);
  }
  return result;
}
}  // namespace config_detail

inline std::vector<Route> load_config(const std::string & path)
{
  using namespace config_detail;
  std::ifstream file(path);
  if (!file) {
    throw std::invalid_argument("cannot open bridge config: " + path);
  }
  std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
  if (text.empty() || text.size() > 1024 * 1024) {
    throw std::invalid_argument("bridge config must be between 1 byte and 1 MiB");
  }
  std::unique_ptr<json_tokener, decltype(&json_tokener_free)> parser(
    json_tokener_new(), json_tokener_free);
  json_tokener_set_flags(parser.get(), JSON_TOKENER_STRICT);
  std::unique_ptr<json_object, decltype(&json_object_put)> root(
    json_tokener_parse_ex(parser.get(), text.c_str(), static_cast<int>(text.size() + 1)),
    json_object_put);
  if (json_tokener_get_error(parser.get()) != json_tokener_success) {
    throw std::invalid_argument("invalid JSON in " + path);
  }
  fields(root.get(), {"version", "topics"});
  if (json_object_get_int64(field(root.get(), "version", json_type_int)) != 1) {
    throw std::invalid_argument("unsupported bridge config version");
  }
  auto * topics = field(root.get(), "topics", json_type_array);
  const auto count = json_object_array_length(topics);
  if (count == 0 || count > 128) {
    throw std::invalid_argument("require 1..128 topic routes");
  }
  std::vector<Route> routes;
  std::set<std::string> host_topics, guest_topics;
  for (std::size_t i = 0; i < count; ++i) {
    auto * entry = json_object_array_get_idx(topics, i);
    fields(entry, {"host_topic", "guest_topic", "type", "direction", "qos"});
    Route route;
    route.host_topic = string(entry, "host_topic");
    route.guest_topic = string(entry, "guest_topic");
    route.type = string(entry, "type");
    route.direction = string(entry, "direction");
    if (route.host_topic.front() != '/' || route.guest_topic.front() != '/') {
      throw std::invalid_argument("topic names must be absolute");
    }
    if (
      !host_topics.insert(route.host_topic).second ||
      !guest_topics.insert(route.guest_topic).second) {
      throw std::invalid_argument("each topic may appear only once per side (avoid echo loops)");
    }
    if (route.direction != "host_to_guest" && route.direction != "guest_to_host") {
      throw std::invalid_argument("direction must be host_to_guest or guest_to_host");
    }
    auto * qos = field(entry, "qos", json_type_object);
    fields(qos, {"history", "depth", "reliability", "durability"});
    const auto history = string(qos, "history");
    const auto reliability = string(qos, "reliability");
    const auto durability = string(qos, "durability");
    const auto depth = json_object_get_int64(field(qos, "depth", json_type_int));
    if (history != "keep_last" || depth < 1 || depth > 128) {
      throw std::invalid_argument("supported history is keep_last with depth 1..128");
    }
    route.qos = rclcpp::QoS(rclcpp::KeepLast(static_cast<std::size_t>(depth)));
    if (reliability == "reliable") {
      route.qos.reliable();
    } else if (reliability == "best_effort") {
      route.qos.best_effort();
    } else {
      throw std::invalid_argument("reliability must be reliable or best_effort");
    }
    if (durability == "volatile") {
      route.qos.durability_volatile();
    } else if (durability == "transient_local") {
      route.qos.transient_local();
    } else {
      throw std::invalid_argument("durability must be volatile or transient_local");
    }
    // Length-prefix every field so the wire identity is unambiguous and independent
    // of JSON whitespace, object ordering, or route array ordering.
    for (const auto & part :
         {route.host_topic, route.guest_topic, route.type, route.direction, history,
          std::to_string(depth), reliability, durability}) {
      route.key += std::to_string(part.size()) + ":" + part;
    }
    routes.push_back(std::move(route));
  }
  return routes;
}
}  // namespace wall_follow_bridge
