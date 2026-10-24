#pragma once

#include <chimaera_dds_transport/trace.hpp>
#include <cstring>
#include <vector>

namespace chimaera_dds::trace
{
struct Part
{
  std::string id;
  uint32_t first{1}, count{1}, total{1};
};

// Inspect RTPS metadata only. No payload copy, deserialization, DDS take, or
// changes to the datagram. Fragment ranges are retained until offline analysis.
inline std::vector<Part> parts(const uint8_t * bytes, std::size_t size)
{
  using namespace eprosima::fastrtps::rtps;
  std::vector<Part> result;
  if (size < 20 || std::memcmp(bytes, "RTPS", 4)) return result;
  GuidPrefix_t prefix;
  std::memcpy(prefix.value, bytes + 8, 12);
  for (std::size_t offset = 20; offset < size;) {
    if (size - offset < 4) return {};
    const auto kind = bytes[offset], flags = bytes[offset + 1];
    const bool little = flags & 1;
    const auto read16 = [little](const uint8_t * p) -> uint32_t {
      return little ? uint32_t(p[0]) | uint32_t(p[1]) << 8 : uint32_t(p[1]) | uint32_t(p[0]) << 8;
    };
    const auto read32 = [little](const uint8_t * p) {
      uint32_t value = 0;
      for (unsigned i = 0; i < 4; ++i) value |= uint32_t(p[i]) << (8 * (little ? i : 3 - i));
      return value;
    };
    const auto body = offset + 4;
    const auto length = read16(bytes + offset + 2);
    // Zero length DATA/DATA_FRAG extends through the remaining datagram.
    const auto end = length ? body + length : (kind == 0x15 || kind == 0x16 ? size : body);
    if (end > size) return {};
    const auto * p = bytes + body;
    if (kind == 0x0c) {  // INFO_SRC replaces the source participant prefix.
      if (end - body < 20) return {};
      std::memcpy(prefix.value, p + 8, 12);
    } else if (kind == 0x15 || kind == 0x16) {
      if (end - body < (kind == 0x15 ? 20u : 32u)) return {};
      // Built-in discovery and DATA containing only dispose/inline QoS are not
      // application sample transfers. DATA_FRAG has a KeyFlag, not a DataFlag.
      if (!(p[11] & 0xc0) && (kind == 0x16 || (flags & 4))) {
        SampleIdentity sample;
        sample.writer_guid().guidPrefix = prefix;
        std::memcpy(sample.writer_guid().entityId.value, p + 8, 4);
        sample.sequence_number().high = static_cast<int32_t>(read32(p + 12));
        sample.sequence_number().low = read32(p + 16);
        if (
          sample.sequence_number().high < 0 ||
          (!sample.sequence_number().high && !sample.sequence_number().low))
          return {};
        Part part{identity(sample)};
        if (kind == 0x16) {
          part.first = read32(p + 20);
          part.count = read16(p + 24);
          const auto fragment = read16(p + 26), payload = read32(p + 28);
          if (!fragment || !payload) return {};
          part.total = 1 + (payload - 1) / fragment;
          if (
            !part.first || !part.count || part.first > part.total ||
            part.count > part.total - part.first + 1)
            return {};
        }
        result.push_back(std::move(part));
      }
    }
    offset = end;
  }
  return result;
}
}  // namespace chimaera_dds::trace
