#pragma once

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <sstream>
#include <stdexcept>
#include <string>

namespace ball_catching
{
// Called with Gazebo at the throw boundary and gem5 paused. The serial timing
// controller has completed its guest step before requesting this physics step.
inline void beginDetailedThrow(const std::string & endpoint)
{
  if (endpoint.empty()) return;  // Native execution.
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (endpoint.size() >= sizeof(address.sun_path))
    throw std::runtime_error("Detailed timing socket path too long");
  std::memcpy(address.sun_path, endpoint.c_str(), endpoint.size() + 1);
  struct Socket {
    int fd{::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0)};
    ~Socket() { if (fd >= 0) ::close(fd); }
  } socket;
  auto fail = [] { throw std::runtime_error("Detailed throw barrier: " + std::string(std::strerror(errno))); };
  if (socket.fd < 0) fail();
  const timeval timeout{30, 0};
  if (::setsockopt(socket.fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0 ||
      ::setsockopt(socket.fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) < 0) fail();
  if (::connect(socket.fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) fail();
  const std::string command = "BENCHMARK_BEGIN\n";
  size_t sent = 0;
  while (sent < command.size()) {
    const auto count = ::send(socket.fd, command.data() + sent, command.size() - sent, MSG_NOSIGNAL);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) fail();
    sent += count;
  }
  std::string reply;
  while (reply.size() < 4096) {
    char byte;
    const auto count = ::recv(socket.fd, &byte, 1, 0);
    if (count < 0 && errno == EINTR) continue;
    if (count < 0) fail();
    if (count == 0) throw std::runtime_error("Detailed timing server disconnected before launch");
    if (byte == '\n') {
      std::istringstream input(reply);
      std::string status, tick, extra;
      if (!(input >> status >> tick) || status != "READY" ||
          tick.find_first_not_of("0123456789") != std::string::npos || (input >> extra))
        throw std::runtime_error("Detailed throw rejected: " + reply);
      return;
    }
    reply += byte;
  }
  throw std::runtime_error("Oversized detailed throw reply");
}
}  // namespace ball_catching
