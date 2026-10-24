#include "latest_sample.hpp"

#include <iostream>
#include <stdexcept>

using namespace chimaera_dds;
void require(bool value, const char * reason)
{
  if (!value) throw std::runtime_error(reason);
}
std::vector<uint8_t> frame(uint32_t sequence, bool little = true)
{
  std::vector<uint8_t> rtps(48);
  std::memcpy(rtps.data(), "RTPS", 4);
  rtps[8] = 1;
  rtps[20] = 0x15;
  rtps[21] = little ? 5 : 4;
  auto number = [&](unsigned offset, uint32_t value, unsigned bytes) {
    for (unsigned i = 0; i < bytes; ++i)
      rtps[offset + i] = uint8_t(value >> (8 * (little ? i : bytes - i - 1)));
  };
  number(22, 24, 2);
  number(26, 16, 2);
  rtps[35] = 3;
  number(40, sequence, 4);
  return wire::packet(
    wire::data, wire::local(1), wire::local(2, 0x20000001), rtps.data(), rtps.size());
}
int main()
{
  try {
    auto eligible = [](uint32_t port) { return port == 0x20000001; };
    using Batch = std::vector<std::vector<uint8_t>>;
    for (bool little : {false, true}) {
      Batch batch{frame(1, little), frame(3, little), frame(2, little), frame(3, little)};
      require(
        retain_latest(batch, eligible) == std::vector<bool>({false, true, false, false}),
        "latest sequence selection or duplicate/reordering handling failed");
      require(
        retain_latest(batch, [](uint32_t) { return false; }) == std::vector<bool>(4, true),
        "non-opted-in route was coalesced");
      auto other = batch[1];
      for (unsigned byte : {28u, 36u, 60u, 80u, 84u}) {
        // Different destination port/process, source prefix, reader or writer.
        other = batch[1];
        ++other[byte];
        require(
          retain_latest(Batch{batch[0], other}, [](uint32_t) { return true; }) ==
            std::vector<bool>({true, true}),
          "independent RTPS flows were combined");
      }
      for (unsigned flag : {2u, 0xc0u}) {
        other = batch[1];
        if (flag == 2)
          other[wire::header_bytes + 21] |= 2;  // Inline QoS.
        else
          other[wire::header_bytes + 35] |= 0xc0;  // Built-in writer.
        require(!latest_sample(other.data(), other.size()), "unsafe DATA was coalescible");
      }
      other = batch[1];
      other[wire::header_bytes + 20] = 0x16;
      require(!latest_sample(other.data(), other.size()), "fragment was coalescible");
      other = batch[1];
      other.insert(other.end(), {0x07, 1, 0, 0});  // HEARTBEAT must reach DDS.
      require(!latest_sample(other.data(), other.size()), "reliability metadata was discarded");
      other = batch[1];
      other.insert(other.end(), batch[0].begin() + wire::header_bytes + 20, batch[0].end());
      require(!latest_sample(other.data(), other.size()), "multi-sample datagram was coalescible");
      for (std::size_t size = 0; size < batch[0].size(); ++size)
        require(!latest_sample(batch[0].data(), size), "truncated sample was coalescible");
      // A separate batch owns a new decision; no old sequence state survives
      // checkpoint restore or participant recreation.
      require(
        retain_latest(Batch{batch[0]}, eligible) == std::vector<bool>({true}),
        "coalescing state escaped its batch");
    }
    std::cout
      << "Latest-state batches, independent flows and conservative RTPS exclusions passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
