#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <utility>

namespace chimaera_dds
{
// Copies share storage. After handing a packet to a router/controller, treat
// its bytes as immutable until every owner has released it. An external owner
// may hold a DMA loan; the last queued recipient releases that loan.
class Packet
{
  std::shared_ptr<void> owner_;
  uint8_t * data_{};
  std::size_t size_{};

public:
  Packet() = default;
  static Packet adopt(void * data, std::size_t size, std::shared_ptr<void> owner)
  {
    if (!data || !owner) throw std::invalid_argument("DDS packet requires storage and owner");
    Packet packet;
    packet.owner_ = std::move(owner);
    packet.data_ = static_cast<uint8_t *>(data);
    packet.size_ = size;
    return packet;
  }
  // Every byte must be filled before the packet is exposed to another stage.
  static Packet for_overwrite(std::size_t size)
  {
    auto storage = std::shared_ptr<uint8_t>(new uint8_t[size], std::default_delete<uint8_t[]>());
    return adopt(storage.get(), size, storage);
  }
  uint8_t * data() { return data_; }
  const uint8_t * data() const { return data_; }
  std::size_t size() const { return size_; }
  const std::shared_ptr<void> & owner() const { return owner_; }
};
}  // namespace chimaera_dds
