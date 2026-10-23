#pragma once

#include <algorithm>
#include <chimaera_dds_transport/packet.hpp>
#include <string>

#include "protocol.hpp"

namespace chimaera_dds
{
// A paused timing guest can take seconds to consume one host interval. Fast
// DDS continues its wall-clock discovery retransmissions meanwhile. Collapse
// only semantically identical pending HEARTBEAT/ACKNACK packets: retain the
// newest RTPS count, without replacing DATA, changed ranges/bitmaps, or flags.
inline std::string repeated_control_key(const Packet & packet)
{
  const auto * data = packet.data();
  const auto size = packet.size();
  if (
    size < wire::header_bytes + 20 || size > 4096 || data[5] != wire::data ||
    wire::read32(data + 28) & application_port_bit ||
    std::memcmp(data + wire::header_bytes, "RTPS", 4))
    return {};
  std::string key(reinterpret_cast<const char *>(data), size);
  bool control = false;
  for (std::size_t offset = wire::header_bytes + 20; offset < size;) {
    if (size - offset < 4) return {};
    const auto kind = data[offset], flags = data[offset + 1];
    const auto length = flags & 1 ? unsigned(data[offset + 2]) | unsigned(data[offset + 3]) << 8
                                  : unsigned(data[offset + 3]) | unsigned(data[offset + 2]) << 8;
    const auto body = offset + 4;
    if (length > size - body) return {};
    if (kind == 0x07 || kind == 0x06) {
      if (kind == 0x07 && length != 28) return {};
      if (kind == 0x06) {
        if (length < 24) return {};
        const auto * bits = data + body + 16;
        const auto count = flags & 1 ? wire::read32(bits)
                                     : uint32_t(bits[3]) | uint32_t(bits[2]) << 8 |
                                         uint32_t(bits[1]) << 16 | uint32_t(bits[0]) << 24;
        if (count > 256 || length != 24 + 4 * ((count + 31) / 32)) return {};
      }
      std::fill(key.begin() + body + length - 4, key.begin() + body + length, '\0');
      control = true;
    } else if (kind == 0x09) {  // INFO_TS is informational for these control messages.
      if (length != ((flags & 2) ? 0u : 8u)) return {};
      std::fill(key.begin() + body, key.begin() + body + length, '\0');
    } else if (kind == 0x0e) {
      if (length != 12) return {};  // INFO_DST must retain its participant identity.
    } else {
      return {};
    }
    offset = body + length;
  }
  return control ? key : std::string{};
}
}  // namespace chimaera_dds
