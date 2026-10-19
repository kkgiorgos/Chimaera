#include <dlfcn.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/u_int64_multi_array.hpp>
#include <std_msgs/msg/u_int8_multi_array.hpp>
#include <thread>
#include <vector>

#include "shared_clock.hpp"

namespace chimaera_latency
{
using Packet = std_msgs::msg::UInt8MultiArray;
using Records = std_msgs::msg::UInt64MultiArray;
// All integers on ROS data routes have an explicit little-endian encoding.
constexpr size_t header_bytes = 40;
constexpr uint64_t packet_magic = 0x3141544144424c43ULL;
constexpr size_t event_fields = 7;
using Event = std::array<uint64_t, event_fields>;

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
      output_
        << "side,event,sequence,scheduled_ns,publish_ns,callback_ns,physics_ns,payload_bytes\n";
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
    publisher_ = create_publisher<Packet>("/latency/" + outgoing + "/" + suffix, qos);
    subscription_ = create_subscription<Packet>(
      "/latency/" + incoming + "/" + suffix, qos,
      [this](Packet::ConstSharedPtr packet) { receive(*packet); });
    if (side_ == "host") {
      reports_ = create_subscription<Records>(
        "/latency/guest_events", rclcpp::QoS(128).reliable(),
        [this](Records::ConstSharedPtr records) { report(*records); });
    } else {
      reporter_ = create_publisher<Records>("/latency/guest_events", rclcpp::QoS(128).reliable());
    }
    packet_.data.resize(header_bytes + payload, 0xa5);
    encode(packet_.data, 0, packet_magic);
    events_.reserve(static_cast<size_t>(stop_ / 1e9 * rate_ * 2 + 1024));
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
      publisher_->get_subscription_count() > 0 && subscription_->get_publisher_count() > 0 &&
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
    while (!frozen_ && time < stop_ && scheduled(sequence_) <= time) {
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
    for (size_t i = 1; i < event.size(); ++i) output_ << ',' << event[i];
    output_ << '\n';
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
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(endpoint);
    while (rclcpp::ok()) {
      executor.spin_some();
      endpoint->tick();
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  } catch (const std::exception & error) {
    std::cerr << "Latency endpoint: " << error.what() << '\n';
    result = 1;
  }
  rclcpp::shutdown();
  return result;
}
