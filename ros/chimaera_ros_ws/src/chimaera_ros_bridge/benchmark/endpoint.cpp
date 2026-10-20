#include <dlfcn.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <rosgraph_msgs/msg/clock.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/u_int64_multi_array.hpp>
#include <std_msgs/msg/u_int8_multi_array.hpp>
#include <thread>
#include <type_traits>
#include <vector>

#include "shared_clock.hpp"

namespace chimaera_latency
{
using Packet = std_msgs::msg::UInt8MultiArray;
using Records = std_msgs::msg::UInt64MultiArray;
// All integers on ROS data routes have an explicit little-endian encoding.
constexpr size_t header_bytes = 40;
constexpr uint64_t packet_magic = 0x3141544144424c43ULL;
constexpr size_t event_fields = 8;
using Event = std::array<uint64_t, event_fields>;
const std::array<std::string, 6> stream_names = {"synthetic",    "left_image",     "right_image",
                                                 "joint_states", "effort_command", "clock"};

struct Stream
{
  double rate{};
  uint64_t sequence{}, bytes{};
  std::function<uint64_t(uint64_t)> publish;
};
template <typename Message>
auto & stamp(Message & message)
{
  if constexpr (std::is_same_v<std::remove_const_t<Message>, rosgraph_msgs::msg::Clock>)
    return message.clock;
  else
    return message.header.stamp;
}

void encode(std::vector<uint8_t> & bytes, size_t field, uint64_t value)
{
  for (size_t i = 0; i < 8; ++i) bytes[field * 8 + i] = value >> (8 * i);
}
uint64_t decode(const std::vector<uint8_t> & bytes, size_t field)
{
  uint64_t value = 0;
  for (size_t i = 0; i < 8; ++i) value |= uint64_t(bytes[field * 8 + i]) << (8 * i);
  return value;
}

class Endpoint : public rclcpp::Node
{
public:
  Endpoint() : Node("latency_endpoint")
  {
    side_ = declare_parameter<std::string>("side", "host");
    if (side_ != "host" && side_ != "guest") throw std::invalid_argument("Invalid side");
    const bool bridged = declare_parameter<bool>("bridged", false);
    const auto clock_kind = declare_parameter<std::string>("clock_kind", "shared");
    const auto clock_file = declare_parameter<std::string>("clock_file", "");
    origin_file_ =
      declare_parameter<std::string>("origin_file", "/tmp/chimaera-sim-time-origin.ns");
    if (clock_kind == "shared") {
      clock_ = std::make_unique<SharedClock>(clock_file);
    } else if (clock_kind == "gem5") {
      const auto library =
        declare_parameter<std::string>("gem5_clock_library", "libchimaera_gem5_clock_addr.so");
      library_ = dlopen(library.c_str(), RTLD_NOW | RTLD_LOCAL);
      if (!library_) throw std::runtime_error(dlerror());
      gem5_now_ = reinterpret_cast<uint64_t (*)()>(dlsym(library_, "chimaera_gem5_time_ns"));
      if (!gem5_now_) throw std::runtime_error("Missing gem5 clock symbol");
    } else {
      throw std::invalid_argument("Invalid clock_kind");
    }
    const auto physics_file = declare_parameter<std::string>("physics_clock_file", "");
    if (!physics_file.empty()) physics_ = std::make_unique<SharedClock>(physics_file);
    ball_ = declare_parameter<std::string>("workload", "synthetic") == "ball_catching";
    const auto policy = declare_parameter<std::string>("delivery_policy", "latest");
    if (policy != "latest" && policy != "lossless")
      throw std::invalid_argument("Invalid delivery policy");
    lossless_ = policy == "lossless";
    verify_payload_ = declare_parameter<bool>("verify_payload", false);
    const auto payload = declare_parameter<int64_t>("payload_bytes", 64);
    rate_ = declare_parameter<double>("rate_hz", 20.0);
    const auto warmup = declare_parameter<int64_t>("warmup_ns", 500000000);
    const auto duration = declare_parameter<int64_t>("duration_ns", 2000000000);
    const auto drain = declare_parameter<int64_t>("drain_ns", 500000000);
    if (
      payload < 0 || payload > 16 * 1024 * 1024 || !std::isfinite(rate_) || rate_ <= 0 ||
      rate_ > 10000 || warmup < 0 || duration <= 0 || drain <= 0 ||
      warmup + duration > 3600000000000LL || (warmup + duration) / 1e9 * rate_ > 1000000) {
      throw std::invalid_argument("Invalid benchmark size, rate, duration or sample bound");
    }
    stop_ = warmup + duration;
    cutoff_ = stop_ + drain;
    ready_file_ = declare_parameter<std::string>("ready_file", "");
    done_file_ = declare_parameter<std::string>("done_file", "");
    const auto output = declare_parameter<std::string>("output_file", "");
    if (side_ == "host") {
      output_.open(output);
      if (!output_) throw std::runtime_error("Cannot open events output");
      output_ << "side,event,sequence,scheduled_ns,publish_ns,callback_ns,physics_ns,payload_bytes,"
                 "stream\n";
    }
    const auto reliability = declare_parameter<std::string>("reliability", "reliable");
    const auto depth = declare_parameter<int64_t>("depth", 128);
    if ((reliability != "reliable" && reliability != "best_effort") || depth < 1 || depth > 128)
      throw std::invalid_argument("Invalid QoS");
    auto qos = rclcpp::QoS(depth).durability_volatile();
    if (reliability == "best_effort") qos.best_effort();
    const std::string suffix = side_ == "guest" && bridged ? "guest" : "host";
    const std::string outgoing = side_ == "host" ? "h2g" : "g2h";
    const std::string incoming = side_ == "host" ? "g2h" : "h2g";
    if (ball_) {
      setup_ball(bridged);
    } else {
      publisher_ = create_publisher<Packet>("/latency/" + outgoing + "/" + suffix, qos);
      subscription_ = create_subscription<Packet>(
        "/latency/" + incoming + "/" + suffix, qos,
        [this](Packet::ConstSharedPtr packet) { receive(*packet); });
      publishers_.push_back(publisher_);
      subscriptions_.push_back(subscription_);
    }
    if (side_ == "host") {
      reports_ = create_subscription<Records>(
        "/latency/guest_events", rclcpp::QoS(128).reliable(),
        [this](Records::ConstSharedPtr records) { report(*records); });
    } else {
      reporter_ = create_publisher<Records>("/latency/guest_events", rclcpp::QoS(128).reliable());
    }
    if (!ball_) {
      packet_.data.resize(header_bytes + payload, 0xa5);
      encode(packet_.data, 0, packet_magic);
    }
    double event_rate = ball_ ? 0. : rate_ * 2;
    for (const auto & item : streams_) event_rate += item.second.rate;
    events_.reserve(static_cast<size_t>(stop_ / 1e9 * event_rate + 1024));
  }
  ~Endpoint() override
  {
    if (library_) dlclose(library_);
  }

  uint64_t now()
  {
    if (clock_) return clock_->now();
    if (!origin_) {
      std::ifstream input(origin_file_);
      uint64_t value;
      if (!(input >> value)) return SharedClock::unset;
      origin_ = value;
    }
    const auto value = gem5_now_();
    if (value < *origin_) throw std::runtime_error("gem5 clock precedes experiment origin");
    return value - *origin_;
  }
  void tick()
  {
    const bool peers =
      std::all_of(
        publishers_.begin(), publishers_.end(),
        [](const auto & p) { return p->get_subscription_count() > 0; }) &&
      std::all_of(
        subscriptions_.begin(), subscriptions_.end(),
        [](const auto & p) { return p->get_publisher_count() > 0; }) &&
      (reporter_ ? reporter_->get_subscription_count() > 0 : reports_->get_publisher_count() > 0);
    if (!ready_ && peers) {
      ready_ = true;
      if (!ready_file_.empty()) std::ofstream(ready_file_) << "ready\n";
    }
    if (!ready_) return;
    auto time = now();
    if (time == SharedClock::unset) return;
    // An independent source, never an echo. A frozen/jumping clock releases a
    // burst with actual publish times; scheduled times remain in every record.
    if (ball_) tick_ball(time);
    while (!ball_ && !frozen_ && time < stop_ && scheduled(sequence_) <= time) {
      encode(packet_.data, 1, sequence_);
      encode(packet_.data, 2, scheduled(sequence_));
      const auto published = now();
      if (published >= stop_) break;
      encode(packet_.data, 3, published);
      encode(packet_.data, 4, packet_.data.size() - header_bytes);
      const auto physics = physics_ ? physics_->now() : SharedClock::unset;
      publisher_->publish(packet_);
      events_.push_back(
        {0, sequence_, scheduled(sequence_), published, SharedClock::unset, physics,
         packet_.data.size() - header_bytes});
      ++sequence_;
      time = now();
    }
    if (time >= cutoff_ && !frozen_) {
      frozen_ = true;
      if (side_ == "host") {
        for (const auto & event : events_) write("host", event);
        output_.flush();
        events_.clear();
      }
    }
    if (side_ == "guest" && frozen_ && !exported_) export_chunk();
    finish();
  }

private:
  static uint64_t release(uint64_t sequence, double rate)
  {
    return static_cast<uint64_t>(std::llround(sequence * 1e9 / rate));
  }
  template <typename Message>
  void typed(
    uint64_t id, const std::string & topic, double rate, size_t depth, bool bridged,
    Message example)
  {
    auto & stream = streams_[id];
    stream.rate = rate;
    rclcpp::SerializedMessage serialized;
    rclcpp::Serialization<Message> codec;
    codec.serialize_message(&example, &serialized);
    stream.bytes = serialized.size();
    const bool outgoing = (side_ == "host") == (id != 4);
    const auto name =
      "/latency/ball/" + topic + "/" + (side_ == "guest" && bridged ? "guest" : "host");
    auto qos = rclcpp::QoS(depth).best_effort().durability_volatile();
    // Application queues differ from bridge queues. Robot sensor consumers
    // use SensorDataQoS (depth 5); the effort receiver uses depth 1. Synthetic
    // camera/clock producers use the ROS/Gazebo publisher queue depth of 10.
    if (outgoing) qos = (id <= 2 || id == 5) ? rclcpp::QoS(10).reliable() : rclcpp::SensorDataQoS();
    if (lossless_) qos = rclcpp::QoS(128).reliable().durability_volatile();
    if (outgoing) {
      auto publisher = create_publisher<Message>(name, qos);
      publishers_.push_back(publisher);
      auto message = std::make_shared<Message>(std::move(example));
      stream.publish = [this, publisher, message](uint64_t scheduled) {
        stamp(*message).sec = scheduled / 1000000000ULL;
        stamp(*message).nanosec = scheduled % 1000000000ULL;
        if constexpr (std::is_same_v<Message, sensor_msgs::msg::Image>) {
          if (verify_payload_) {
            for (unsigned i = 0; i < 8; ++i) {
              message->data[i] = static_cast<uint8_t>(scheduled >> (8 * i));
              message->data[message->data.size() - 8 + i] = message->data[i] ^ 0xff;
            }
          }
        }
        const auto published = now();
        if (published >= stop_) return SharedClock::unset;
        publisher->publish(*message);
        return published;
      };
    } else {
      subscriptions_.push_back(
        create_subscription<Message>(name, qos, [this, id](std::shared_ptr<const Message> message) {
          const auto received = now();  // Callback entry, before inspecting the message.
          const auto physics = physics_ ? physics_->now() : SharedClock::unset;
          if (received == SharedClock::unset || frozen_ || received > cutoff_) return;
          const auto & header = stamp(*message);
          if (header.sec < 0) throw std::runtime_error("Invalid capture timestamp");
          const uint64_t capture = uint64_t(header.sec) * 1000000000ULL + header.nanosec;
          const auto & stream = streams_.at(id);
          const auto sequence = static_cast<uint64_t>(std::llround(capture * stream.rate / 1e9));
          if (release(sequence, stream.rate) != capture)
            throw std::runtime_error("Message capture stamp does not identify a source sample");
          if constexpr (std::is_same_v<Message, sensor_msgs::msg::Image>) {
            if (verify_payload_) {
              if (
                message->encoding != "rgb8" || message->step != message->width * 3 ||
                message->data.size() != message->height * message->step)
                throw std::runtime_error("Image dimensions/encoding corrupted");
              for (std::size_t i = 0; i < message->data.size(); ++i) {
                const auto expected =
                  i < 8 ? static_cast<uint8_t>(capture >> (8 * i))
                  : i >= message->data.size() - 8
                    ? static_cast<uint8_t>(
                        (capture >> (8 * (i - (message->data.size() - 8)))) ^ 0xff)
                    : 0xa5;
                if (message->data[i] != expected)
                  throw std::runtime_error(
                    "Image payload corrupted or buffer reused before delivery");
              }
            }
          }
          if (events_.size() >= 4000000)
            throw std::runtime_error("Benchmark event capacity exceeded");
          // Actual publication time is joined from buffered source records after
          // export. Actual ROS messages carry no benchmark envelope or extra bytes.
          events_.push_back(
            {1, sequence, capture, SharedClock::unset, received, physics, stream.bytes, id});
        }));
    }
  }
  void setup_ball(bool bridged)
  {
    const auto width = declare_parameter<int64_t>("camera_width", 640);
    const auto height = declare_parameter<int64_t>("camera_height", 480);
    const auto camera_hz = declare_parameter<double>("camera_hz", 90.);
    const auto joint_hz = declare_parameter<double>("joint_hz", 250.);
    const auto control_hz = declare_parameter<double>("control_hz", 250.);
    const auto physics_step = declare_parameter<int64_t>("physics_step_ns", 1000000);
    if (width < 64 || height < 64 || width * height * 3 > 16 * 1024 * 1024 || physics_step < 100000)
      throw std::invalid_argument("Invalid ball-catching dimensions or physics step");
    for (double rate : {camera_hz, joint_hz, control_hz})
      if (!std::isfinite(rate) || rate <= 0 || rate > 10000)
        throw std::invalid_argument("Invalid ball-catching source rate");
    sensor_msgs::msg::Image image;
    image.width = width;
    image.height = height;
    image.step = width * 3;
    image.encoding = "rgb8";
    image.data.resize(width * height * 3, 0xa5);
    image.header.frame_id = "left_camera";
    typed(1, "left_image", camera_hz, 5, bridged, image);
    image.header.frame_id = "right_camera";
    typed(2, "right_image", camera_hz, 5, bridged, std::move(image));
    sensor_msgs::msg::JointState joints, command;
    for (size_t i = 1; i <= 7; ++i) {
      joints.name.push_back("fr3_joint" + std::to_string(i));
      command.name.push_back("fr3_joint" + std::to_string(i));
    }
    joints.position.resize(7, 0.);
    joints.velocity.resize(7, 0.);
    command.effort.resize(7, 0.);
    typed(3, "joint_states", joint_hz, 5, bridged, std::move(joints));
    typed(4, "effort_command", control_hz, 1, bridged, std::move(command));
    typed(5, "clock", 1e9 / physics_step, 1, bridged, rosgraph_msgs::msg::Clock{});
    for (const auto & item :
         std::vector<std::pair<std::string, double>>{{"ready", 25.}, {"perception_ready", 10.}}) {
      const auto topic =
        "/latency/ball/" + item.first + "/" + (side_ == "guest" && bridged ? "guest" : "host");
      if (side_ == "guest") {
        auto publisher = create_publisher<std_msgs::msg::Bool>(topic, rclcpp::QoS(1).reliable());
        publishers_.push_back(publisher);
        Stream stream;
        stream.rate = item.second;
        stream.publish = [publisher](uint64_t) {
          std_msgs::msg::Bool message;
          message.data = true;
          publisher->publish(message);
          return uint64_t(0);
        };
        background_.push_back(std::move(stream));
      } else {
        subscriptions_.push_back(create_subscription<std_msgs::msg::Bool>(
          topic, rclcpp::QoS(1).reliable(), [](std_msgs::msg::Bool::ConstSharedPtr) {}));
      }
    }
  }
  void tick_ball(uint64_t time)
  {
    if (frozen_ || time >= stop_) return;
    // Merge source schedules so stereo pairs are emitted together, including
    // when advancing a paused clock makes several frames due at once.
    while (time < stop_) {
      auto next = streams_.end();
      for (auto it = streams_.begin(); it != streams_.end(); ++it)
        if (
          it->second.publish && release(it->second.sequence, it->second.rate) <= time &&
          (next == streams_.end() || release(it->second.sequence, it->second.rate) <
                                       release(next->second.sequence, next->second.rate)))
          next = it;
      if (next == streams_.end()) break;
      auto & stream = next->second;
      const auto capture = release(stream.sequence, stream.rate);
      const auto physics = physics_ ? physics_->now() : SharedClock::unset;
      const auto published = stream.publish(capture);
      if (published == SharedClock::unset) break;
      events_.push_back(
        {0, stream.sequence++, capture, published, SharedClock::unset, physics, stream.bytes,
         next->first});
      time = now();
    }
    for (auto & stream : background_)
      while (time < stop_ && release(stream.sequence, stream.rate) <= time) {
        stream.publish(release(stream.sequence++, stream.rate));
        time = now();
      }
  }
  uint64_t scheduled(uint64_t sequence) const
  {
    return static_cast<uint64_t>(std::llround(sequence * 1e9 / rate_));
  }
  void receive(const Packet & packet)
  {
    const auto received = now();  // Callback entry, before parsing or allocating records.
    const auto physics = physics_ ? physics_->now() : SharedClock::unset;
    if (received == SharedClock::unset || frozen_ || received > cutoff_) return;
    if (
      packet.data.size() < header_bytes || decode(packet.data, 0) != packet_magic ||
      decode(packet.data, 4) != packet.data.size() - header_bytes) {
      throw std::runtime_error("Malformed benchmark packet");
    }
    const auto published = decode(packet.data, 3);
    if (received < published)
      throw std::runtime_error("Negative simulation latency: clock alignment failed");
    if (events_.size() >= 4000000) throw std::runtime_error("Benchmark event capacity exceeded");
    events_.push_back(
      {1, decode(packet.data, 1), decode(packet.data, 2), published, received, physics,
       decode(packet.data, 4)});
  }
  void export_chunk()
  {
    Records chunk;
    // Header: chunk sequence, final flag, total event count. Empty final chunk
    // establishes a complete export even when every data message was lost.
    const auto end = std::min(export_position_ + 128, events_.size());
    const bool final = end == events_.size();
    chunk.data = {export_sequence_++, static_cast<uint64_t>(final), events_.size()};
    for (; export_position_ < end; ++export_position_) {
      const auto & row = events_[export_position_];
      chunk.data.insert(chunk.data.end(), row.begin(), row.end());
    }
    reporter_->publish(chunk);
    exported_ = final;
  }
  void write(const std::string & side, const Event & event)
  {
    output_ << side << ',' << (event[0] == 0 ? "send" : "receive");
    for (size_t i = 1; i < event.size() - 1; ++i) output_ << ',' << event[i];
    if (event[7] >= stream_names.size()) throw std::runtime_error("Invalid event stream");
    output_ << ',' << stream_names[event[7]] << '\n';
  }
  void report(const Records & chunk)
  {
    if (
      guest_done_ || chunk.data.size() < 3 || chunk.data[0] != expected_chunk_++ ||
      chunk.data[1] > 1 || (chunk.data.size() - 3) % event_fields != 0) {
      throw std::runtime_error("Invalid or incomplete guest event export");
    }
    if (guest_total_ && *guest_total_ != chunk.data[2])
      throw std::runtime_error("Guest export count changed");
    guest_total_ = chunk.data[2];
    for (size_t pos = 3; pos < chunk.data.size(); pos += event_fields) {
      Event event;
      std::copy_n(chunk.data.begin() + pos, event_fields, event.begin());
      if (event[0] > 1) throw std::runtime_error("Invalid exported event kind");
      write("guest", event);
      ++guest_count_;
    }
    if (chunk.data[1]) {
      if (guest_count_ != *guest_total_)
        throw std::runtime_error("Guest event export was truncated");
      guest_done_ = true;
    }
  }
  void finish()
  {
    if (side_ == "host" && frozen_ && guest_done_ && !done_) {
      output_.flush();
      if (!output_) throw std::runtime_error("Failed writing events");
      std::ofstream(done_file_) << "complete\n";
      done_ = true;
    }
  }
  bool ball_{};
  bool lossless_{};
  bool verify_payload_{};
  std::map<uint64_t, Stream> streams_;
  std::vector<Stream> background_;
  std::vector<rclcpp::PublisherBase::SharedPtr> publishers_;
  std::vector<rclcpp::SubscriptionBase::SharedPtr> subscriptions_;
  std::string side_, origin_file_, ready_file_, done_file_;
  std::unique_ptr<SharedClock> clock_, physics_;
  void * library_{};
  uint64_t (*gem5_now_)(){};
  std::optional<uint64_t> origin_, guest_total_;
  double rate_{};
  uint64_t stop_{}, cutoff_{}, sequence_{}, expected_chunk_{}, guest_count_{};
  size_t export_position_{}, export_sequence_{};
  bool ready_{}, frozen_{}, exported_{}, guest_done_{}, done_{};
  Packet packet_;
  std::vector<Event> events_;
  std::ofstream output_;
  rclcpp::Publisher<Packet>::SharedPtr publisher_;
  rclcpp::Subscription<Packet>::SharedPtr subscription_;
  rclcpp::Publisher<Records>::SharedPtr reporter_;
  rclcpp::Subscription<Records>::SharedPtr reports_;
};
}  // namespace chimaera_latency

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int result = 0;
  try {
    auto endpoint = std::make_shared<chimaera_latency::Endpoint>();
    // Keep source scheduling periodic while receiving subscriptions wake the
    // executor directly. An explicit sleep after spin_some delayed every
    // receive and drained bursts one sample per OS tick in the guest.
    auto source_timer =
      endpoint->create_wall_timer(std::chrono::milliseconds(1), [&] { endpoint->tick(); });
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(endpoint);
    executor.spin();
  } catch (const std::exception & error) {
    std::cerr << "Latency endpoint: " << error.what() << '\n';
    result = 1;
  }
  rclcpp::shutdown();
  return result;
}
