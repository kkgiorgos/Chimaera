#include "receive_batch.hpp"

#include <iostream>
#include <stdexcept>

#include "rtps_control.hpp"

using chimaera::Message;
using namespace chimaera_dds;
void require(bool value, const char * reason)
{
  if (!value) throw std::runtime_error(reason);
}
int main()
{
  try {
    std::array<std::byte, 128> storage{};
    unsigned taken = 0, released = 0, clock = 0;
    auto batch = drain_control(
      [&] {
        require(taken == released, "RX slots retained across reaps");
        if (taken == 3) return std::vector<Message>{};
        storage[0] = std::byte(++taken);
        auto loan = std::shared_ptr<void>(storage.data(), [&](void *) {
          ++released;
          storage[0] = std::byte{0xff};
        });
        std::vector<Message> result;
        result.push_back(Message::adopt(storage.data(), storage.size(), loan));
        return result;
      },
      [&] { return ++clock; });
    require(released == 3 && batch.messages.size() == 3, "queued groups not drained");
    for (unsigned i = 0; i < 3; ++i) {
      require(batch.messages[i][0] == std::byte(i + 1), "returned RX storage was reused");
      require(batch.arrivals[i] == i + 1, "arrival timestamp changed during aggregation");
    }
    taken = 0;
    batch = drain_control(
      [&] {
        ++taken;
        std::vector<Message> result;
        for (unsigned i = 0; i < 8; ++i) result.push_back(Message::for_overwrite(128));
        return result;
      },
      [] { return 42; });
    require(taken == 8 && batch.messages.size() == 64, "continuous ingress starves DDS");
    taken = 0;
    batch = drain_control(
      [&] {
        ++taken;
        std::vector<Message> result;
        result.push_back(Message::for_overwrite(256 * 1024));
        return result;
      },
      [] { return 42; });
    require(taken == 1, "large control frames exceed the snapshot byte budget");
    std::cout << "RX snapshot ownership, timestamps and finite service budgets passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
