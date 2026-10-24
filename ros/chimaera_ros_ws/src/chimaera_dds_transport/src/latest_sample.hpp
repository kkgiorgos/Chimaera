#pragma once

#include <algorithm>
#include <array>
#include <map>
#include <optional>

#include "protocol.hpp"

namespace chimaera_dds
{
struct LatestSample
{
  std::array<uint8_t, 52> flow{};  // Destination locator, writer, reader and INFO_DST.
  uint64_t sequence{};
};
// Coalescing is deliberately narrower than an RTPS parser: exactly one complete
// user DATA sample, with no inline QoS, fragments or reliability/control work.
// Unknown or malformed datagrams retain ordinary FIFO delivery.
inline std::optional<LatestSample> latest_sample(const uint8_t * frame, std::size_t size)
{
  if (size < wire::header_bytes + 20 || frame[5] != wire::data) return {};
  const auto * bytes = frame + wire::header_bytes;
  const auto length = size - wire::header_bytes;
  if (std::memcmp(bytes, "RTPS", 4)) return {};
  LatestSample result;
  std::copy_n(frame + 28, 20, result.flow.begin());
  std::array<uint8_t, 12> prefix{};
  std::copy_n(bytes + 8, 12, prefix.begin());
  bool found = false;
  for (std::size_t offset = 20; offset < length;) {
    if (length - offset < 4) return {};
    const auto kind = bytes[offset], flags = bytes[offset + 1];
    const bool little = flags & 1;
    auto read = [little](const uint8_t * p, unsigned count) {
      uint32_t value = 0;
      for (unsigned i = 0; i < count; ++i)
        value |= uint32_t(p[i]) << (8 * (little ? i : count - i - 1));
      return value;
    };
    const auto body = offset + 4;
    const auto count = read(bytes + offset + 2, 2);
    const auto end = count ? body + count : (kind == 0x15 ? length : body);
    if (end > length) return {};
    const auto * p = bytes + body;
    if (kind == 0x15) {
      if (
        found || end - body <= 20 || !(flags & 4) || (flags & 2) || (p[11] & 0xc0) ||
        read(p + 2, 2) != 16)
        return {};
      const auto high = read(p + 12, 4), low = read(p + 16, 4);
      if ((high & 0x80000000U) || (!high && !low)) return {};
      std::copy(prefix.begin(), prefix.end(), result.flow.begin() + 20);
      std::copy_n(p + 8, 4, result.flow.begin() + 32);  // Writer entity.
      std::copy_n(p + 4, 4, result.flow.begin() + 36);  // Target reader entity.
      result.sequence = uint64_t(high) << 32 | low;
      found = true;
    } else if (kind == 0x0c) {  // INFO_SRC
      if (found || end - body != 20) return {};
      std::copy_n(p + 8, 12, prefix.begin());
    } else if (kind == 0x0e) {  // INFO_DST
      if (found || end - body != 12) return {};
      std::copy_n(p, 12, result.flow.begin() + 40);
    } else if (kind == 0x09) {  // INFO_TS, including an invalidated timestamp.
      if (end - body != ((flags & 2) ? 0u : 8u)) return {};
    } else if (kind != 0x01) {  // PAD is the only other harmless submessage.
      return {};
    }
    offset = end;
  }
  return found ? std::optional<LatestSample>(result) : std::nullopt;
}

template <class Batch, class Eligible>
std::vector<bool> retain_latest(const Batch & batch, Eligible eligible)
{
  std::vector<bool> keep(batch.size(), true);
  std::map<std::array<uint8_t, 52>, std::pair<uint64_t, std::size_t>> latest;
  for (std::size_t i = 0; i < batch.size(); ++i) {
    const auto * frame = reinterpret_cast<const uint8_t *>(batch[i].data());
    if (batch[i].size() < wire::header_bytes || !eligible(wire::read32(frame + 28))) continue;
    const auto sample = latest_sample(frame, batch[i].size());
    if (!sample) continue;
    auto [entry, inserted] = latest.emplace(sample->flow, std::make_pair(sample->sequence, i));
    if (!inserted) {
      if (sample->sequence > entry->second.first) {
        keep[entry->second.second] = false;
        entry->second = {sample->sequence, i};
      } else {
        keep[i] = false;
      }
    }
  }
  return keep;
}
}  // namespace chimaera_dds
