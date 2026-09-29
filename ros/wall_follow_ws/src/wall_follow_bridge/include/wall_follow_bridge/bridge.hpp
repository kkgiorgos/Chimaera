#pragma once

#include <ament_index_cpp/get_package_share_directory.hpp>
#include <chimaera/controller.hpp>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <map>
#include <rclcpp/generic_publisher.hpp>
#include <rclcpp/generic_subscription.hpp>
#include <set>
#include <wall_follow_bridge/config.hpp>

namespace wall_follow_bridge
{
// ROS and controller callbacks share one thread. Each transport frame contains
// a versioned route identity followed by one unchanged ROS serialized message.
class Bridge : public rclcpp::Node, public chimaera::DataProducer, public chimaera::DataConsumer
{
public:
  explicit Bridge(
    const std::string & side, const rclcpp::NodeOptions & options = rclcpp::NodeOptions())
  : Node(side + "_bridge", options)
  {
    if (side != "host" && side != "guest") {
      throw std::invalid_argument("invalid bridge side");
    }
    auto path = declare_parameter<std::string>("config_file", "");
    if (path.empty()) {
      path =
        ament_index_cpp::get_package_share_directory("wall_follow_bridge") + "/config/bridge.json";
    }
    const auto routes = load_config(path);
    std::set<std::string> resolved_topics;
    // Validate remappings before creating endpoints: aliases must not form loops.
    for (const auto & route : routes) {
      const auto topic = get_node_topics_interface()->resolve_topic_name(
        side == "host" ? route.host_topic : route.guest_topic);
      if (!resolved_topics.insert(topic).second) {
        throw std::invalid_argument("remapped bridge topics overlap: " + topic);
      }
    }
    for (const auto & route : routes) {
      const auto & topic = side == "host" ? route.host_topic : route.guest_topic;
      const bool outgoing = (side == "host") == (route.direction == "host_to_guest");
      if (outgoing) {
        subscriptions_.push_back(create_generic_subscription(
          topic, route.type, route.qos,
          [this, key = route.key](std::shared_ptr<rclcpp::SerializedMessage> message) {
            if (message->size() > max_bytes || outgoing_.size() >= max_messages) {
              throw std::runtime_error(
                "bridge outgoing queue or serialized message limit exceeded");
            }
            chimaera::Message bytes(8 + key.size() + message->size());
            std::memcpy(bytes.data(), "CBR1", 4);
            const auto length = static_cast<std::uint32_t>(key.size());
            for (unsigned i = 0; i < 4; ++i) {
              bytes[4 + i] = static_cast<std::byte>(length >> (24 - 8 * i));
            }
            std::memcpy(bytes.data() + 8, key.data(), key.size());
            std::memcpy(
              bytes.data() + 8 + key.size(), message->get_rcl_serialized_message().buffer,
              message->size());
            outgoing_.push_back(std::move(bytes));
          }));
      } else {
        publishers_.emplace(route.key, create_generic_publisher(topic, route.type, route.qos));
      }
      RCLCPP_INFO(
        get_logger(), "%s %s [%s] (%s)", outgoing ? "Send" : "Receive", topic.c_str(),
        route.type.c_str(), route.direction.c_str());
    }
  }

  std::optional<chimaera::Message> take() override
  {
    // Pump even when guest run_next() stays inside an interval for many polls.
    if (pump) {
      pump();
    }
    if (outgoing_.empty()) {
      return std::nullopt;
    }
    auto bytes = std::move(outgoing_.front());
    outgoing_.pop_front();
    ++transmitted_;
    return bytes;
  }

  void submit(chimaera::Message bytes) override
  {
    if (bytes.size() < 8 || std::memcmp(bytes.data(), "CBR1", 4) != 0) {
      throw std::runtime_error("invalid bridge frame/version");
    }
    std::uint32_t length = 0;
    for (unsigned i = 0; i < 4; ++i) {
      length = (length << 8) | std::to_integer<std::uint32_t>(bytes[4 + i]);
    }
    if (length > 2048 || length > bytes.size() - 8) {
      throw std::runtime_error("invalid bridge route header length");
    }
    const std::string key(reinterpret_cast<const char *>(bytes.data() + 8), length);
    const auto publisher = publishers_.find(key);
    if (publisher == publishers_.end()) {
      throw std::runtime_error(
        "unknown incoming route/direction or mismatched bridge configuration");
    }
    const auto size = bytes.size() - 8 - length;
    if (size < 4 || size > max_bytes) {
      throw std::runtime_error("invalid incoming serialized message size");
    }
    rclcpp::SerializedMessage message(size);
    auto & serialized = message.get_rcl_serialized_message();
    std::memcpy(serialized.buffer, bytes.data() + 8 + length, size);
    serialized.buffer_length = size;
    publisher->second->publish(message);
    ++received_;
    last_receive_ = std::chrono::steady_clock::now();
  }

  std::uint64_t transmitted() const { return transmitted_; }
  std::uint64_t received() const { return received_; }
  std::size_t pending() const { return outgoing_.size(); }
  std::size_t local_talkers() const
  {
    std::size_t count = 0;
    for (const auto & subscription : subscriptions_) {
      count += subscription->get_publisher_count();
    }
    return count;
  }
  std::size_t local_listeners() const
  {
    std::size_t count = 0;
    for (const auto & publisher : publishers_) {
      count += publisher.second->get_subscription_count();
    }
    return count;
  }
  std::chrono::steady_clock::time_point last_receive() const { return last_receive_; }

  static constexpr std::size_t max_bytes = 128 * 1024;
  static constexpr std::size_t max_messages = 128;
  std::function<void()> pump;

private:
  std::deque<chimaera::Message> outgoing_;
  std::uint64_t transmitted_{};
  std::uint64_t received_{};
  std::chrono::steady_clock::time_point last_receive_{};
  std::map<std::string, rclcpp::GenericPublisher::SharedPtr> publishers_;
  std::vector<rclcpp::GenericSubscription::SharedPtr> subscriptions_;
};
}  // namespace wall_follow_bridge
