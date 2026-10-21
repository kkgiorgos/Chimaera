#pragma once

#include <poll.h>
#include <sys/eventfd.h>
#include <unistd.h>

#include <cerrno>
#include <cstdint>
#include <stdexcept>
#include <system_error>

namespace chimaera_ros_bridge
{
// The DDS coordinator waits on the driver directly. Router notifications, loan
// release failures and ROS shutdown share a persistent, coalescing wakeup.
class TransportWait
{
public:
  TransportWait() : wake_(::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK))
  {
    if (wake_ < 0) fail("create transport wakeup");
  }
  ~TransportWait() { ::close(wake_); }
  TransportWait(const TransportWait &) = delete;
  TransportWait & operator=(const TransportWait &) = delete;

  void notify() const
  {
    const uint64_t one = 1;
    ssize_t count;
    do {
      count = ::write(wake_, &one, sizeof(one));
    } while (count < 0 && errno == EINTR);
    if (count < 0 && errno != EAGAIN) fail("signal transport wakeup");
  }
  void wait(int completion_fd) const
  {
    pollfd descriptors[2]{{completion_fd, POLLIN, 0}, {wake_, POLLIN, 0}};
    int result;
    do {
      result = ::poll(descriptors, 2, -1);
    } while (result < 0 && errno == EINTR);
    if (result < 0) fail("wait for transport readiness");
    for (const auto & descriptor : descriptors)
      if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))
        throw std::runtime_error("transport readiness descriptor failed");
    if (descriptors[1].revents & POLLIN) {
      uint64_t value;
      ssize_t count;
      do {
        count = ::read(wake_, &value, sizeof(value));
      } while (count < 0 && errno == EINTR);
      if (count < 0 && errno != EAGAIN) fail("consume transport wakeup");
    }
  }

private:
  [[noreturn]] static void fail(const char * operation)
  {
    throw std::system_error(errno, std::generic_category(), operation);
  }
  int wake_;
};
}  // namespace chimaera_ros_bridge
