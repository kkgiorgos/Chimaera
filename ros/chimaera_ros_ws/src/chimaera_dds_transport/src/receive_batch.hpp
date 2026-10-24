#pragma once

#include <chimaera/message.hpp>
#include <cstring>
#include <vector>

namespace chimaera_dds
{
struct ReceiveBatch
{
  std::vector<chimaera::Message> messages;
  std::vector<uint64_t> arrivals;
};

// A physical RX pool is smaller than the admitted FIFO behind it. Return its
// loans before DDS dispatch so already-queued retries and state samples can be
// considered together. Every arrival keeps its original reap timestamp. The
// snapshot has a finite work/memory budget even if ingress never becomes empty.
template <class Take, class Clock>
ReceiveBatch drain_control(Take take, Clock clock)
{
  ReceiveBatch result;
  std::size_t bytes = 0;
  for (unsigned reap = 0; reap < 8; ++reap) {
    auto loans = take();
    if (loans.empty()) break;
    const auto arrived = clock();
    for (const auto & loan : loans) {
      auto message = chimaera::Message::for_overwrite(loan.size());
      std::memcpy(message.data(), loan.data(), loan.size());
      bytes += loan.size();
      result.messages.push_back(std::move(message));
      result.arrivals.push_back(arrived);
    }
    // Destruction returns the complete physical group before the next reap.
    // Never wait for more traffic, and keep DDS/shutdown work from starving.
    if (result.messages.size() >= 64 || bytes >= 256 * 1024) break;
  }
  return result;
}
}  // namespace chimaera_dds
