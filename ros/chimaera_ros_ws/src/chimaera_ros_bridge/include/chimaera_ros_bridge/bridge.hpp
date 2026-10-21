#pragma once

#include <chimaera/controller.hpp>
#include <chimaera/outgoing_queue.hpp>
#include <chimaera_dds_transport/router.hpp>
#include <chimaera_ros_bridge/config.hpp>
#include <chimaera_ros_bridge/serialized_view.hpp>
#include <chimaera_ros_bridge/stereo_queue.hpp>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <map>
#include <rclcpp/generic_publisher.hpp>
#include <rclcpp/generic_subscription.hpp>
#include <set>
#include <thread>

namespace chimaera_ros_bridge
{
// ROS and controller callbacks share one thread. Each transport frame contains
// a versioned route identity followed by unchanged ROS serialization. Opt-in
// stereo groups wrap two frames into one atomic, coalesced controller entry.
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
    if (
      byte_limit < 4 || byte_limit > 64 * 1024 * 1024 - 2056 || queue_limit < 1 ||
      queue_limit > 1000000) {
      throw std::invalid_argument("invalid serialized byte or pending message limit");
    }
    byte_limit_ = static_cast<std::size_t>(byte_limit);
    queue_limit_ = static_cast<std::size_t>(queue_limit);
    image_age_ns_ = declare_parameter<int64_t>("max_image_age_ns", 0);
    image_diagnostics_ = declare_parameter<bool>("image_diagnostics", false);
    if (image_age_ns_ < 0 || image_age_ns_ > 3600000000000LL) {
      throw std::invalid_argument("invalid max_image_age_ns");
    }
    outgoing_ = chimaera::control::OutgoingQueue(queue_limit_, queue_limit_ * (byte_limit_ + 2056));
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
    const auto middleware = declare_parameter<std::string>("middleware_transport", "bridge");
    if (middleware != "bridge" && middleware != "fastdds")
      throw std::invalid_argument("middleware_transport must be bridge or fastdds");
    if (middleware == "fastdds") {
      router_ = std::make_unique<chimaera_dds::Router>(
        declare_parameter<std::string>("dds_socket", "/tmp/chimaera-dds-" + side + ".sock"),
        side == "host" ? 1 : 2);
      for (const auto & route : routes)
        local_endpoints_.emplace_back(
          side == "host" ? route.host_topic : route.guest_topic,
          (side == "host") == (route.direction == "host_to_guest"));
      RCLCPP_INFO(get_logger(), "Fast DDS middleware routing enabled (%lu routes)", routes.size());
      return;
    }
    for (const auto & route : routes) {
      const auto & topic = side == "host" ? route.host_topic : route.guest_topic;
      if (!route.coalesce_group.empty()) {
        stereo_.add(route.coalesce_group, route.key, route.coalesce);
        stereo_groups_[route.key] = route.coalesce_group;
      }
      const bool outgoing = (side == "host") == (route.direction == "host_to_guest");
      if (outgoing) {
        subscriptions_.push_back(create_generic_subscription(
          topic, route.type, route.qos,
          [this, key = route.key, coalesce = route.coalesce,
           group = route.coalesce_group](std::shared_ptr<rclcpp::SerializedMessage> message) {
            ++callback_generation_;
            if (message->size() > byte_limit_) {
              throw std::runtime_error(
                "bridge outgoing queue or serialized message limit exceeded");
            }
            auto bytes = allocate(8 + key.size() + message->size());
            std::memcpy(bytes.data(), "CBR1", 4);
            const auto length = static_cast<std::uint32_t>(key.size());
            for (unsigned i = 0; i < 4; ++i) {
              bytes[4 + i] = static_cast<std::byte>(length >> (24 - 8 * i));
            }
            std::memcpy(bytes.data() + 8, key.data(), key.size());
            std::memcpy(
              bytes.data() + 8 + key.size(), message->get_rcl_serialized_message().buffer,
              message->size());
            if (group.empty()) {
              outgoing_.submit(
                std::move(bytes), coalesce ? std::string_view(key) : std::string_view{});
              return;
            }
            const auto stamp = image_stamp(
              reinterpret_cast<const std::byte *>(message->get_rcl_serialized_message().buffer),
              message->size());
            if (auto pair = stereo_.submit(group, key, stamp, std::move(bytes))) {
              // One controller entry makes replacement and worker-thread drains
              // atomic for both cameras. Inner CBR1 payloads remain unchanged.
              auto packed = allocate(8 + (*pair)[0].size() + (*pair)[1].size());
              std::memcpy(packed.data(), "CBP1", 4);
              const auto first = static_cast<uint32_t>((*pair)[0].size());
              for (unsigned i = 0; i < 4; ++i)
                packed[4 + i] = static_cast<std::byte>(first >> (24 - 8 * i));
              std::memcpy(packed.data() + 8, (*pair)[0].data(), first);
              std::memcpy(packed.data() + 8 + first, (*pair)[1].data(), (*pair)[1].size());
              outgoing_.submit(std::move(packed), coalesce ? "pair:" + group : "");
            }
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
    if (router_) {
      auto packet = router_->take();
      if (!packet) return std::nullopt;
      auto bytes = chimaera::Message::adopt(
        reinterpret_cast<std::byte *>(packet->data()), packet->size(), packet->owner());
      ++transmitted_;
      return bytes;
    }
    auto entry = outgoing_.take();
    if (!entry) {
      return std::nullopt;
    }
    ++transmitted_;
    return std::move(entry->data);
  }

  void submit(chimaera::Message bytes)
  {
    if (on_receive) on_receive(bytes);
    if (router_) {
      auto owner = std::make_shared<chimaera::Message>(std::move(bytes));
      router_->receive(chimaera_dds::Packet::adopt(owner->data(), owner->size(), owner));
      ++received_;
      last_receive_ = std::chrono::steady_clock::now();
      return;
    }
    if (bytes.size() >= 8 && std::memcmp(bytes.data(), "CBP1", 4) == 0) {
      const auto first = length_at(bytes.data() + 4);
      if (first > bytes.size() - 8) throw std::runtime_error("invalid stereo frame length");
      const auto left = frame(bytes.data() + 8, first);
      const auto right = frame(bytes.data() + 8 + first, bytes.size() - 8 - first);
      const auto group = stereo_groups_.find(left.key);
      if (
        group == stereo_groups_.end() || left.key == right.key ||
        !stereo_groups_.count(right.key) || group->second != stereo_groups_.at(right.key)) {
        throw std::runtime_error("mismatched stereo group");
      }
      const auto stamp = image_stamp(left.data, left.size);
      if (stamp != image_stamp(right.data, right.size))
        throw std::runtime_error("mismatched stereo timestamps");
      if (!fresh_image(stamp, "received")) {
        stale_image();
        return;
      }
      SerializedView left_message(left.data, left.size);
      SerializedView right_message(right.data, right.size);
      if (!fresh_image(stamp, "publish_ready")) {
        stale_image();
        return;
      }
      publishers_.at(left.key)->publish(left_message.message());
      publishers_.at(right.key)->publish(right_message.message());
      if (image_diagnostics_) trace_image("published", stamp);
      received_ += 2;
      last_receive_ = std::chrono::steady_clock::now();
      return;
    }
    const auto value = frame(bytes.data(), bytes.size());
    if (stereo_groups_.count(value.key))
      throw std::runtime_error("stereo route requires paired delivery");
    SerializedView message(value.data, value.size);
    publishers_.at(value.key)->publish(message.message());
    ++received_;
    last_receive_ = std::chrono::steady_clock::now();
  }

private:
  struct Frame
  {
    std::string key;
    const std::byte * data;
    std::size_t size;
  };
  static uint32_t length_at(const std::byte * bytes)
  {
    uint32_t length = 0;
    for (unsigned i = 0; i < 4; ++i) length = (length << 8) | std::to_integer<uint32_t>(bytes[i]);
    return length;
  }
  Frame frame(const std::byte * bytes, std::size_t byte_count) const
  {
    if (byte_count < 8 || std::memcmp(bytes, "CBR1", 4) != 0)
      throw std::runtime_error("invalid bridge frame/version");
    const auto length = length_at(bytes + 4);
    if (length > 2048 || length > byte_count - 8)
      throw std::runtime_error("invalid bridge route header length");
    const std::string key(reinterpret_cast<const char *>(bytes + 8), length);
    if (!publishers_.count(key))
      throw std::runtime_error(
        "unknown incoming route/direction or mismatched bridge configuration");
    const auto size = byte_count - 8 - length;
    if (size < 4 || size > byte_limit_)
      throw std::runtime_error("invalid incoming serialized message size");
    return {key, bytes + 8 + length, size};
  }
  std::optional<int64_t> trace_image(const char * stage, int64_t stamp)
  {
    const auto time = simulation_time ? simulation_time() : std::nullopt;
    if (image_diagnostics_ && time) {
      auto & last = image_trace_[stage];
      if (last.first++ < 3 || *time - last.second >= 100000000) {
        last.second = *time;
        std::fprintf(
          stderr,
          "CHIMAERA_IMAGE component=bridge stage=%s capture_ns=%ld now_ns=%ld age_ns=%ld "
          "budget_ns=%ld\n",
          stage, stamp, *time, *time - stamp, image_age_ns_);
      }
    }
    return time;
  }
  bool fresh_image(int64_t stamp, const char * stage)
  {
    if (image_age_ns_ == 0 && !image_diagnostics_) return true;
    const auto time = trace_image(stage, stamp);
    if (image_age_ns_ == 0) return true;
    return time && *time >= stamp && *time - stamp <= image_age_ns_;
  }
  void stale_image()
  {
    if (++stale_pairs_ == 1) {
      RCLCPP_WARN(get_logger(), "Discarding stale stereo pairs; maximum age %ld ns", image_age_ns_);
    }
  }

public:
  // spin_some collects readiness once. Recollect after callbacks consumed a
  // sample so bursts can drain in this exchange. Bound passes even if sources
  // remain continuously ready, and track callbacks rather than queue size:
  // latest-value replacement does not change the latter.
  template <typename Executor>
  void drain_callbacks(Executor & executor, std::size_t max_passes = 32)
  {
    for (std::size_t pass = 0; pass < max_passes; ++pass) {
      const auto previous = callback_generation_;
      executor.spin_some();
      if (callback_generation_ == previous) break;
    }
  }
  // Application-driven transfer, shared by host boundaries and guest polls.
  void exchange(chimaera::DataController & controller)
  {
    while (auto bytes = controller.take()) {
      submit(std::move(*bytes));
    }
    // Service callbacks once per exchange, after delivery and before draining.
    if (pump) {
      pump();
    }
    if (router_) {
      while (auto packet = take()) controller.submit(std::move(*packet));
      return;
    }
    while (auto entry = outgoing_.take()) {
      ++transmitted_;
      controller.submit(std::move(entry->data), entry->key);
    }
  }

  std::uint64_t transmitted() const { return transmitted_; }
  std::uint64_t received() const { return received_; }
  std::uint64_t stale_pairs() const { return stale_pairs_; }
  std::size_t unmatched_images() const { return stereo_.pending(); }
  std::size_t pending() const { return router_ ? router_->pending() : outgoing_.size(); }
  bool middleware_routing() const { return bool(router_); }
  std::size_t controller_queue_limit() const
  {
    // RTPS discovery/reliability adds a burst of small packets at fresh boot.
    // Keep the controller's 32 MiB byte bound and the legacy application limit.
    return router_ ? 8192 : chimaera::control::max_messages;
  }
  void set_transport_notify(std::function<void()> notify)
  {
    if (router_) router_->set_notify(std::move(notify));
  }
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
    if (router_) {
      router_->check();
      for (const auto & endpoint : local_endpoints_)
        if (!router_->endpoint_ready(endpoint.first, endpoint.second)) return false;
      return true;
    }
    for (const auto & subscription : subscriptions_) {
      if (subscription->get_publisher_count() == 0) {
        return false;
      }
    }
    for (const auto & publisher : publishers_) {
      if (publisher.second->get_subscription_count() == 0) {
        return false;
      }
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
  std::function<chimaera::Message(std::size_t)> allocate = chimaera::Message::for_overwrite;
  std::function<void(const chimaera::Message &)> on_receive;
  std::function<std::optional<int64_t>()> simulation_time;

private:
  std::size_t byte_limit_{max_bytes}, queue_limit_{max_messages};
  chimaera::control::OutgoingQueue outgoing_;
  std::uint64_t callback_generation_{};
  std::uint64_t transmitted_{};
  std::uint64_t received_{};
  int64_t image_age_ns_{};
  uint64_t stale_pairs_{};
  bool image_diagnostics_{};
  std::map<std::string, std::pair<uint64_t, int64_t>> image_trace_;
  StereoQueue stereo_;
  std::map<std::string, std::string> stereo_groups_;
  std::chrono::steady_clock::time_point last_receive_{};
  std::map<std::string, rclcpp::GenericPublisher::SharedPtr> publishers_;
  std::vector<rclcpp::GenericSubscription::SharedPtr> subscriptions_;
  std::unique_ptr<chimaera_dds::Router> router_;
  std::vector<std::pair<std::string, bool>> local_endpoints_;
};
}  // namespace chimaera_ros_bridge
