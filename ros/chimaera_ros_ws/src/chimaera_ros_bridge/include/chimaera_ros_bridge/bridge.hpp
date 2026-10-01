#pragma once

#include <chimaera/controller.hpp>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <map>
#include <rclcpp/generic_publisher.hpp>
#include <rclcpp/generic_subscription.hpp>
#include <set>
#include <thread>
#include <chimaera_ros_bridge/config.hpp>

namespace chimaera_ros_bridge
{
// ROS and controller callbacks share one thread. Each transport frame contains
// a versioned route identity followed by one unchanged ROS serialized message.
class Bridge : public rclcpp::Node
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
      throw std::invalid_argument("config_file is required");
    }
    const auto byte_limit = declare_parameter<int64_t>("max_serialized_bytes", max_bytes);
    const auto queue_limit = declare_parameter<int64_t>("max_pending_messages", max_messages);
    if (byte_limit < 4 || byte_limit > 64 * 1024 * 1024 - 2056 ||
      queue_limit < 1 || queue_limit > 1000000) {
      throw std::invalid_argument("invalid serialized byte or pending message limit");
    }
    byte_limit_ = static_cast<std::size_t>(byte_limit);
    queue_limit_ = static_cast<std::size_t>(queue_limit);
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
            if (message->size() > byte_limit_) {
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
            if (outgoing_.size() >= queue_limit_) {
              throw std::runtime_error(
                "bridge outgoing queue or serialized message limit exceeded");
            }
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

  std::optional<chimaera::Message> take()
  {
    if (outgoing_.empty()) {
      return std::nullopt;
    }
    auto bytes = std::move(outgoing_.front());
    outgoing_.pop_front();
    ++transmitted_;
    return bytes;
  }

  void submit(chimaera::Message bytes)
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
    if (size < 4 || size > byte_limit_) {
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

  // Application-driven transfer, shared by host boundaries and guest polls.
  void exchange(chimaera::DataController & controller)
  {
    while (auto bytes = controller.take()) {submit(std::move(*bytes));}
    // Service callbacks once per exchange, after delivery and before draining.
    if (pump) {pump();}
    while (auto bytes = take()) {
      controller.submit(std::move(*bytes));
    }
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
  bool local_routes_ready() const
  {
    for (const auto & subscription : subscriptions_) {
      if (subscription->get_publisher_count() == 0) { return false; }
    }
    for (const auto & publisher : publishers_) {
      if (publisher.second->get_subscription_count() == 0) { return false; }
    }
    return true;
  }

  void wait_for_local_routes(
    std::chrono::seconds timeout, const std::function<bool()> & cancelled = {}) const
  {
    if (timeout.count() <= 0) {
      throw std::invalid_argument("application startup timeout must be positive");
    }
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (!local_routes_ready()) {
      if (cancelled && cancelled()) {
        throw std::runtime_error("Guest application startup cancelled");
      }
      if (std::chrono::steady_clock::now() >= deadline) {
        throw std::runtime_error("Timed out waiting for guest application ROS endpoints");
      }
      // DDS discovers peers independently of the executor. Leave outgoing
      // messages in bounded ROS queues until the host starts transport polling.
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
  }

  std::chrono::steady_clock::time_point last_receive() const { return last_receive_; }

  static constexpr std::size_t max_bytes = 4096;
  static constexpr std::size_t max_messages = 128;
  std::function<void()> pump;

private:
  std::size_t byte_limit_{max_bytes}, queue_limit_{max_messages};
  std::deque<chimaera::Message> outgoing_;
  std::uint64_t transmitted_{};
  std::uint64_t received_{};
  std::chrono::steady_clock::time_point last_receive_{};
  std::map<std::string, rclcpp::GenericPublisher::SharedPtr> publishers_;
  std::vector<rclcpp::GenericSubscription::SharedPtr> subscriptions_;
};
}  // namespace chimaera_ros_bridge
