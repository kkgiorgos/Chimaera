#pragma once

#include <chimaera/controller.hpp>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <stdexcept>
#include <string>

namespace talker_listener_bridge
{

// All ROS and controller callbacks run on the same thread. Wire payloads are
// the exact String.data bytes; the transport already supplies message framing.
class Bridge : public rclcpp::Node, public chimaera::DataProducer, public chimaera::DataConsumer
{
public:
  explicit Bridge(const std::string & side) : Node(side + "_bridge")
  {
    const auto outgoing = declare_parameter<std::string>("outgoing_topic", "/" + side + "/chatter");
    const auto incoming = declare_parameter<std::string>(
      "incoming_topic", side == "host" ? "/guest/chatter" : "/host/chatter");
    publisher_ = create_publisher<std_msgs::msg::String>(incoming, 128);
    subscription_ = create_subscription<std_msgs::msg::String>(
      outgoing, 128, [this](std_msgs::msg::String::ConstSharedPtr message) {
        if (message->data.size() > max_bytes || outgoing_.size() >= max_messages) {
          throw std::runtime_error("bridge outgoing queue or message limit exceeded");
        }
        chimaera::Message bytes(message->data.size());
        if (!bytes.empty()) {
          std::memcpy(bytes.data(), message->data.data(), bytes.size());
        }
        outgoing_.push_back(std::move(bytes));
      });
    if (std::string(publisher_->get_topic_name()) == subscription_->get_topic_name()) {
      throw std::invalid_argument("incoming and outgoing topics must differ to avoid an echo loop");
    }
  }

  std::optional<chimaera::Message> take() override
  {
    // Guest run_next() can stay inside one interval for many polls. Pump ROS
    // here so talker messages are collected even before that call returns.
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
    if (bytes.size() > max_bytes) {
      throw std::runtime_error("oversized incoming String");
    }
    std_msgs::msg::String message;
    if (!bytes.empty()) {
      message.data.assign(reinterpret_cast<const char *>(bytes.data()), bytes.size());
    }
    publisher_->publish(message);
    ++received_;
    last_receive_ = std::chrono::steady_clock::now();
  }

  std::uint64_t transmitted() const { return transmitted_; }
  std::uint64_t received() const { return received_; }
  std::size_t pending() const { return outgoing_.size(); }
  std::size_t local_talkers() const { return subscription_->get_publisher_count(); }
  std::size_t local_listeners() const { return publisher_->get_subscription_count(); }
  std::chrono::steady_clock::time_point last_receive() const { return last_receive_; }

  static constexpr std::size_t max_bytes = 4096;
  static constexpr std::size_t max_messages = 128;
  std::function<void()> pump;

private:
  std::deque<chimaera::Message> outgoing_;
  std::uint64_t transmitted_{};
  std::uint64_t received_{};
  std::chrono::steady_clock::time_point last_receive_{};
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr subscription_;
};
}  // namespace talker_listener_bridge
