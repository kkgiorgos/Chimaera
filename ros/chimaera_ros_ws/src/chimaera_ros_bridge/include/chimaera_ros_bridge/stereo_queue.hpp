#pragma once

#include <array>
#include <chimaera/controller.hpp>
#include <cstdint>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace chimaera_ros_bridge
{
// Image starts with a CDR encapsulation followed by Header.stamp. Inspect only
// those twelve bytes; the pixel payload stays opaque and is never deserialized.
inline int64_t image_stamp(const std::byte * data, std::size_t size)
{
  if (
    size < 12 || data[0] != std::byte{0} || (data[1] != std::byte{0} && data[1] != std::byte{1})) {
    throw std::runtime_error("expected CDR Image header");
  }
  const bool little = data[1] == std::byte{1};
  auto integer = [&](std::size_t offset) {
    uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i) {
      value = (value << 8) | std::to_integer<uint32_t>(data[offset + (little ? 3 - i : i)]);
    }
    return value;
  };
  const auto seconds = integer(4), nanos = integer(8);
  if (seconds > INT32_MAX || nanos >= 1000000000) {
    throw std::runtime_error("invalid simulation image timestamp");
  }
  return int64_t(seconds) * 1000000000 + nanos;
}

class StereoQueue
{
public:
  using Pair = std::array<chimaera::Message, 2>;
  void add(const std::string & group, const std::string & key, bool latest = true)
  {
    groups_[group].keys.push_back(key);
    groups_[group].latest = latest;
  }
  std::size_t pending() const
  {
    std::size_t count = 0;
    for (const auto & [name, group] : groups_) {
      for (const auto & frames : group.frames) count += frames.size();
    }
    return count;
  }
  std::optional<Pair> submit(
    const std::string & group, const std::string & key, int64_t stamp, chimaera::Message frame)
  {
    auto & state = groups_.at(group);
    if (state.keys.size() != 2) throw std::logic_error("stereo group requires two routes");
    if (key != state.keys[0] && key != state.keys[1])
      throw std::logic_error("unknown stereo route");
    if (stamp <= state.completed) {
      if (!state.latest) throw std::runtime_error("out-of-order lossless stereo sample");
      return std::nullopt;
    }
    const auto side = state.keys[0] == key ? 0u : 1u;
    state.frames[side][stamp] = std::move(frame);
    // Bound unmatched arrivals too, including if one camera disappears.
    for (auto & frames : state.frames) {
      if (!state.latest && frames.size() > 128)
        throw std::runtime_error("lossless stereo unmatched queue limit exceeded");
      if (state.latest)
        while (frames.size() > 3) frames.erase(frames.begin());
    }
    if (!state.frames[1 - side].count(stamp)) return std::nullopt;
    if (
      !state.latest &&
      (state.frames[0].begin()->first != stamp || state.frames[1].begin()->first != stamp))
      throw std::runtime_error("missing lossless stereo sample");
    Pair pair{std::move(state.frames[0].at(stamp)), std::move(state.frames[1].at(stamp))};
    state.completed = stamp;
    for (auto & frames : state.frames) {
      frames.erase(frames.begin(), frames.upper_bound(stamp));
    }
    return pair;
  }

private:
  struct Group
  {
    std::vector<std::string> keys;
    std::array<std::map<int64_t, chimaera::Message>, 2> frames;
    int64_t completed{-1};
    bool latest{true};
  };
  std::map<std::string, Group> groups_;
};
}  // namespace chimaera_ros_bridge
