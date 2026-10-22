#pragma once

#include <fastdds/rtps/common/Locator.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/un.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <chimaera_dds_transport/transport.hpp>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace chimaera_dds::wire
{
using Locator = eprosima::fastrtps::rtps::Locator_t;
constexpr std::size_t header_bytes = cdds_header_bytes;
enum Type : uint8_t
{
  open = 1,
  close = 2,
  data = 3,
  endpoint = 4
};
inline uint32_t read32(const uint8_t * p)
{
  return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
inline void write32(uint8_t * p, uint32_t value)
{
  for (unsigned i = 0; i < 4; ++i) p[i] = uint8_t(value >> (8 * i));
}
inline Locator locator(const uint8_t * p)
{
  Locator value;
  value.kind = transport_kind;
  value.port = read32(p);
  std::memcpy(value.address, p + 4, 16);
  return value;
}
inline void locator(uint8_t * p, const Locator & value)
{
  write32(p, value.port);
  std::memcpy(p + 4, value.address, 16);
}
inline Locator local(unsigned side, unsigned port = 0)
{
  Locator value;
  value.kind = transport_kind;
  value.port = port;
  value.address[0] = uint8_t(side);
  write32(value.address + 4, uint32_t(::getpid()));
  return value;
}
inline std::array<uint8_t, header_bytes> header(
  Type type, const Locator & source, const Locator & destination, std::size_t length = 0)
{
  if (length > configured_max_packet_bytes())
    throw std::length_error("DDS packet exceeds transport limit");
  std::array<uint8_t, header_bytes> result{};
  std::memcpy(result.data(), "CDDS", 4);
  result[4] = 1;
  result[5] = type;
  locator(result.data() + 8, source);
  locator(result.data() + 28, destination);
  write32(result.data() + 48, uint32_t(length));
  return result;
}
inline std::vector<uint8_t> packet(
  Type type, const Locator & source, const Locator & destination, const void * body = nullptr,
  std::size_t length = 0)
{
  const auto prefix = header(type, source, destination, length);
  std::vector<uint8_t> result(header_bytes + length);
  std::memcpy(result.data(), prefix.data(), header_bytes);
  if (length) std::memcpy(result.data() + header_bytes, body, length);
  return result;
}
inline std::size_t length(const uint8_t * p, std::size_t available)
{
  if (available < header_bytes) return 0;
  if (
    std::memcmp(p, "CDDS", 4) || p[4] != 1 || p[5] < open || p[5] > endpoint || p[6] || p[7] ||
    read32(p + 48) > configured_max_packet_bytes())
    throw std::runtime_error("invalid Chimaera DDS frame");
  return header_bytes + read32(p + 48);
}
inline sockaddr_un address(const std::string & path)
{
  sockaddr_un value{};
  value.sun_family = AF_UNIX;
  if (path.empty() || path.size() >= sizeof(value.sun_path))
    throw std::invalid_argument("invalid DDS runtime socket path");
  std::memcpy(value.sun_path, path.c_str(), path.size() + 1);
  return value;
}
inline bool send_all(int fd, const uint8_t * bytes, std::size_t size)
{
  while (size) {
    const auto count = ::send(fd, bytes, size, MSG_NOSIGNAL);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) return false;
    bytes += count;
    size -= std::size_t(count);
  }
  return true;
}
// The caller serializes complete frames on the stream. sendmsg may stop inside
// either segment; advance both iovecs without rebuilding/copying the payload.
inline bool send_frame(
  int fd, const std::array<uint8_t, header_bytes> & prefix, const void * body, std::size_t size)
{
  iovec segments[2]{
    {const_cast<uint8_t *>(prefix.data()), prefix.size()}, {const_cast<void *>(body), size}};
  msghdr message{};
  message.msg_iov = segments;
  message.msg_iovlen = size ? 2 : 1;
  while (message.msg_iovlen) {
    const auto count = ::sendmsg(fd, &message, MSG_NOSIGNAL);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) return false;
    auto remaining = std::size_t(count);
    while (message.msg_iovlen && remaining >= message.msg_iov->iov_len) {
      remaining -= message.msg_iov->iov_len;
      ++message.msg_iov;
      --message.msg_iovlen;
    }
    if (message.msg_iovlen) {
      message.msg_iov->iov_base = static_cast<uint8_t *>(message.msg_iov->iov_base) + remaining;
      message.msg_iov->iov_len -= remaining;
    }
  }
  return true;
}
inline bool receive_all(int fd, uint8_t * bytes, std::size_t size)
{
  while (size) {
    const auto count = ::recv(fd, bytes, size, 0);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) return false;
    bytes += count;
    size -= std::size_t(count);
  }
  return true;
}
}  // namespace chimaera_dds::wire
