#pragma once

#include <chimaera/controller.hpp>
#include <fstream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <vector>

namespace chimaera_ros_bridge
{
// Replay only the host-side readiness prefix. No gem5 instructions execute
// during replay. Groups preserve the commands delivered before each coupled
// physics interval, including readiness flags and telemetry.
class CheckpointReplay
{
public:
  using Batch = std::vector<chimaera::Message>;

  explicit CheckpointReplay(const std::string & path, bool writing) : writing_(writing)
  {
    file_.open(path, std::ios::binary | (writing ? std::ios::out : std::ios::in));
    if (!file_) throw std::runtime_error("Cannot open checkpoint replay: " + path);
    if (writing) {
      file_.write("CHIMRPL1", 8);
    } else {
      char magic[8]{};
      file_.read(magic, 8);
      if (!file_ || std::string(magic, 8) != "CHIMRPL1")
        throw std::runtime_error("Invalid checkpoint replay version");
    }
  }

  void append(const chimaera::Message & frame)
  {
    if (!writing_ || ended_) throw std::logic_error("Replay is not recording");
    if (pending_.size() >= 1024 || frame.size() > 32 * 1024 * 1024 - pending_bytes_)
      throw std::runtime_error("Checkpoint interval exceeds transport bounds");
    pending_.push_back(frame);
    pending_bytes_ += frame.size();
  }

  void boundary()
  {
    put(pending_.size());
    for (const auto & frame : pending_) {
      put(frame.size());
      file_.write(reinterpret_cast<const char *>(frame.data()), frame.size());
    }
    pending_.clear();
    pending_bytes_ = 0;
    if (!file_) throw std::runtime_error("Cannot write checkpoint replay");
  }

  void finish()
  {
    if (!pending_.empty()) throw std::logic_error("Checkpoint splits a physics interval");
    put(std::numeric_limits<uint64_t>::max());
    file_.flush();
    if (!file_) throw std::runtime_error("Cannot finish checkpoint replay");
    ended_ = true;
  }

  std::optional<Batch> next()
  {
    if (writing_ || ended_) throw std::logic_error("Replay is not readable");
    const auto count = get();
    if (count == std::numeric_limits<uint64_t>::max()) {
      ended_ = true;
      if (file_.peek() != std::char_traits<char>::eof())
        throw std::runtime_error("Trailing checkpoint replay data");
      return std::nullopt;
    }
    if (count > 1024) throw std::runtime_error("Invalid checkpoint replay batch size");
    Batch result;
    size_t total = 0;
    for (uint64_t index = 0; index < count; ++index) {
      const auto size = get();
      if (size < 4 || size > 32 * 1024 * 1024 - total)
        throw std::runtime_error("Invalid checkpoint replay frame size");
      total += size;
      chimaera::Message frame(size);
      file_.read(reinterpret_cast<char *>(frame.data()), size);
      if (!file_) throw std::runtime_error("Truncated checkpoint replay frame");
      result.push_back(std::move(frame));
    }
    return result;
  }

private:
  void put(uint64_t value)
  {
    for (int shift = 56; shift >= 0; shift -= 8) file_.put((value >> shift) & 255);
  }
  uint64_t get()
  {
    uint64_t value = 0;
    for (unsigned index = 0; index < 8; ++index) {
      const auto byte = file_.get();
      if (byte == std::char_traits<char>::eof())
        throw std::runtime_error("Truncated checkpoint replay");
      value = (value << 8) | static_cast<unsigned char>(byte);
    }
    return value;
  }
  std::fstream file_;
  bool writing_, ended_{false};
  Batch pending_;
  size_t pending_bytes_{};
};
}  // namespace chimaera_ros_bridge
