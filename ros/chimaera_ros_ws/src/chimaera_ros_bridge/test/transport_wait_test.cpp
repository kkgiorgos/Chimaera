#include <chimaera_ros_bridge/transport_wait.hpp>
#include <chrono>
#include <future>
#include <stdexcept>
#include <thread>

using namespace std::chrono_literals;

void require(bool value, const char * message)
{
  if (!value) throw std::runtime_error(message);
}

int main()
{
  chimaera_ros_bridge::TransportWait wake;
  const int completion = ::eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
  require(completion >= 0, "cannot create fake DMA readiness");
  auto ready = [&](auto & task) {
    const bool completed = task.wait_for(1s) == std::future_status::ready;
    if (!completed) wake.notify();  // Let a failing waiter join as well.
    require(completed, "transport readiness wakeup lost");
    task.get();
  };
  // A notification before poll remains pending; bursts may coalesce.
  for (unsigned i = 0; i < 64; ++i) wake.notify();
  auto early = std::async(std::launch::async, [&] { wake.wait(completion); });
  ready(early);
  for (unsigned i = 0; i < 64; ++i) {
    auto blocked = std::async(std::launch::async, [&] { wake.wait(completion); });
    require(blocked.wait_for(1ms) == std::future_status::timeout, "stale wakeup retained");
    if (i % 2) {
      // DMA readiness wakes the coordinator without a notifying worker.
      const uint64_t one = 1;
      require(::write(completion, &one, sizeof(one)) == sizeof(one), "DMA signal failed");
    } else {
      wake.notify();  // Router readiness and shutdown use the same path.
    }
    ready(blocked);
    if (i % 2) {
      uint64_t value;
      require(
        ::read(completion, &value, sizeof(value)) == sizeof(value) && value == 1,
        "wait consumed the driver's completion");
    }
  }
  // Simultaneous readiness must preserve the driver's record and clear wake.
  wake.notify();
  const uint64_t one = 1;
  require(::write(completion, &one, sizeof(one)) == sizeof(one), "DMA signal failed");
  wake.wait(completion);
  uint64_t value;
  require(::read(completion, &value, sizeof(value)) == sizeof(value), "DMA record lost");
  ::close(completion);
  bool rejected = false;
  try {
    wake.wait(completion);
  } catch (const std::runtime_error &) {
    rejected = true;
  }
  require(rejected, "closed driver descriptor did not fail");
}
