#include <fastdds/rtps/transport/TransportInterface.h>
#include <fastdds/rtps/transport/TransportReceiverInterface.h>

#include <atomic>
#include <chimaera_dds_transport/packet.hpp>
#include <chimaera_dds_transport/transport.hpp>
#include <chrono>
#include <cstdio>
#include <fastdds/rtps/common/LocatorSelectorEntry.hpp>
#include <map>
#include <mutex>
#include <thread>

#include "protocol.hpp"

namespace chimaera_dds
{
namespace
{
using namespace eprosima::fastrtps::rtps;
using namespace eprosima::fastdds::rtps;

class Sender final : public SenderResource
{
public:
  template <class Function>
  explicit Sender(Function function) : SenderResource(transport_kind)
  {
    send_lambda_ = std::move(function);
  }
};

class Transport final : public TransportInterface
{
  struct Channel
  {
    std::mutex mutex;
    TransportReceiverInterface * receiver{};
    Locator locator;
  };
  Descriptor descriptor_;
  int fd_{-1};
  std::atomic<bool> stopped_{false};
  std::atomic<bool> output_open_{false};
  std::mutex send_mutex_, channels_mutex_;
  std::map<unsigned, std::shared_ptr<Channel>> channels_;
  std::thread reader_;

  bool send_packet(
    wire::Type type, const wire::Locator & source, const wire::Locator & destination,
    const void * body = nullptr, std::size_t size = 0)
  {
    const auto prefix = wire::header(type, source, destination, size);
    std::lock_guard lock(send_mutex_);
    if (stopped_) return false;
    if (wire::send_frame(fd_, prefix, body, size)) return true;
    // A timed-out stream write may contain only part of a frame. Retire the
    // connection so another packet cannot be mistaken for its remaining bytes.
    stopped_ = true;
    ::shutdown(fd_, SHUT_RDWR);
    return false;
  }
  void read()
  {
    try {
      std::array<uint8_t, wire::header_bytes> prefix;
      Packet packet;
      while (!stopped_ && wire::receive_all(fd_, prefix.data(), prefix.size())) {
        const auto size = wire::length(prefix.data(), prefix.size());
        if (packet.size() < size) packet = Packet::for_overwrite(size);
        std::memcpy(packet.data(), prefix.data(), prefix.size());
        if (!wire::receive_all(fd_, packet.data() + wire::header_bytes, size - wire::header_bytes))
          break;
        const auto destination = wire::locator(packet.data() + 28);
        std::shared_ptr<Channel> channel;
        {
          std::lock_guard lock(channels_mutex_);
          auto found = channels_.find(destination.port);
          if (found != channels_.end()) channel = found->second;
        }
        if (channel) {
          if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
            std::fprintf(
              stderr, "Chimaera DDS receive: side=%u port=%u bytes=%zu\n", descriptor_.side,
              destination.port, size - wire::header_bytes);
          std::lock_guard lock(channel->mutex);
          if (channel->receiver)
            channel->receiver->OnDataReceived(
              packet.data() + wire::header_bytes, uint32_t(size - wire::header_bytes),
              channel->locator, wire::locator(packet.data() + 8));
        }
      }
    } catch (const std::exception & error) {
      std::fprintf(stderr, "Chimaera DDS receive: %s\n", error.what());
    }
    stopped_ = true;
    if (fd_ >= 0) ::shutdown(fd_, SHUT_RDWR);
  }
  bool fill(Locator & value, unsigned port) const
  {
    if (!IsLocatorSupported(value)) return false;
    if (!value.port) value.port = port;
    if (!value.address[0]) {
      auto own = wire::local(descriptor_.side, value.port);
      value = own;
    }
    return true;
  }

public:
  explicit Transport(const Descriptor & descriptor)
  : TransportInterface(transport_kind), descriptor_(descriptor)
  {
  }
  ~Transport() override { shutdown(); }
  bool init(const PropertyPolicy *) override
  {
    fd_ = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd_ < 0) return false;
    const auto address = wire::address(descriptor_.socket);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    while (::connect(fd_, reinterpret_cast<const sockaddr *>(&address), sizeof(address))) {
      if (
        (errno != ENOENT && errno != ECONNREFUSED) ||
        std::chrono::steady_clock::now() >= deadline) {
        ::close(fd_);
        fd_ = -1;
        return false;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    timeval timeout{5, 0};
    ::setsockopt(fd_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    reader_ = std::thread([this] { read(); });
    if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
      std::fprintf(stderr, "Chimaera DDS connected: side=%u\n", descriptor_.side);
    return true;
  }
  void shutdown() override
  {
    stopped_ = true;
    if (fd_ >= 0) ::shutdown(fd_, SHUT_RDWR);
    if (reader_.joinable()) reader_.join();
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }
  bool IsInputChannelOpen(const Locator & locator) const override
  {
    return const_cast<Transport *>(this)->has_channel(locator.port);
  }
  bool has_channel(unsigned port)
  {
    std::lock_guard lock(channels_mutex_);
    return channels_.count(port);
  }
  bool IsLocatorSupported(const Locator & locator) const override
  {
    return locator.kind == transport_kind;
  }
  bool is_locator_allowed(const Locator & locator) const override
  {
    return IsLocatorSupported(locator);
  }
  bool transform_remote_locator(const Locator & remote, Locator & result) const override
  {
    if (!IsLocatorSupported(remote)) return false;
    result = remote;
    return true;
  }
  Locator RemoteToMainLocal(const Locator & remote) const override
  {
    if (IsLocatorSupported(remote)) return wire::local(descriptor_.side);
    return Locator(LOCATOR_KIND_INVALID, 0);
  }
  bool OpenOutputChannel(SendResourceList & resources, const Locator & locator) override
  {
    if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
      std::fprintf(
        stderr, "Chimaera DDS output: kind=%d port=%u side=%u\n", locator.kind, locator.port,
        locator.address[0]);
    if (!IsLocatorSupported(locator)) return false;
    if (output_open_.exchange(true)) return true;
    resources.emplace_back(std::make_unique<Sender>(
      [this](
        const octet * bytes, uint32_t size, LocatorsIterator * begin, LocatorsIterator * end,
        const std::chrono::steady_clock::time_point &) {
        if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
          std::fprintf(stderr, "Chimaera DDS send: side=%u bytes=%u\n", descriptor_.side, size);
        bool success = true;
        for (; *begin != *end; ++(*begin)) {
          if (!IsLocatorSupported(**begin)) continue;
          if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
            std::fprintf(
              stderr, "Chimaera DDS destination: port=%u side=%u\n", (**begin).port,
              (**begin).address[0]);
          success =
            send_packet(wire::data, wire::local(descriptor_.side), **begin, bytes, size) && success;
        }
        return success;
      }));
    return true;
  }
  bool OpenInputChannel(
    const Locator & locator, TransportReceiverInterface * receiver, uint32_t) override
  {
    if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
      std::fprintf(
        stderr, "Chimaera DDS input: kind=%d port=%u side=%u\n", locator.kind, locator.port,
        locator.address[0]);
    if (!IsLocatorSupported(locator)) return false;
    auto channel = std::make_shared<Channel>();
    channel->receiver = receiver;
    channel->locator = locator;
    {
      std::lock_guard lock(channels_mutex_);
      if (channels_.count(locator.port)) return false;
      channels_[locator.port] = channel;
    }
    return send_packet(wire::open, wire::local(descriptor_.side), locator);
  }
  bool CloseInputChannel(const Locator & locator) override
  {
    std::shared_ptr<Channel> channel;
    {
      std::lock_guard lock(channels_mutex_);
      const auto found = channels_.find(locator.port);
      if (found == channels_.end()) return false;
      channel = found->second;
      channels_.erase(found);
    }
    {
      std::lock_guard lock(channel->mutex);
      channel->receiver = nullptr;
    }
    return send_packet(wire::close, wire::local(descriptor_.side), locator);
  }
  bool DoInputLocatorsMatch(const Locator & a, const Locator & b) const override
  {
    return a.port == b.port;
  }
  LocatorList NormalizeLocator(const Locator & locator) override
  {
    LocatorList result;
    result.push_back(locator);
    return result;
  }
  void select_locators(LocatorSelector & selector) const override
  {
    auto & entries = selector.transport_starts();
    for (std::size_t index = 0; index < entries.size(); ++index) {
      auto & entry = *entries[index];
      if (!entry.transport_should_process) continue;
      bool selected = false;
      for (std::size_t i = 0; i < entry.unicast.size(); ++i) {
        if (IsLocatorSupported(entry.unicast[i])) {
          entry.state.unicast.push_back(i);
          selected = true;
          break;
        }
      }
      if (!selected)
        for (std::size_t i = 0; i < entry.multicast.size(); ++i) {
          if (IsLocatorSupported(entry.multicast[i])) {
            entry.state.multicast.push_back(i);
            selected = true;
            break;
          }
        }
      if (selected) selector.select(index);
    }
  }
  bool is_local_locator(const Locator & locator) const override
  {
    return locator.address[0] == descriptor_.side;
  }
  TransportDescriptorInterface * get_configuration() override { return &descriptor_; }
  void AddDefaultOutputLocator(LocatorList & locators) override
  {
    locators.push_back(wire::local(descriptor_.side));
  }
  bool getDefaultMetatrafficMulticastLocators(LocatorList & locators, uint32_t port) const override
  {
    Locator value;
    value.kind = transport_kind;
    value.port = port;
    locators.push_back(value);
    return true;
  }
  bool getDefaultMetatrafficUnicastLocators(LocatorList & locators, uint32_t port) const override
  {
    locators.push_back(wire::local(descriptor_.side, port));
    return true;
  }
  // Application endpoints opt into this transport explicitly in the RMW hook.
  bool getDefaultUnicastLocators(LocatorList &, uint32_t) const override { return false; }
  bool fillMetatrafficMulticastLocator(Locator & locator, uint32_t port) const override
  {
    if (!IsLocatorSupported(locator)) return false;
    if (!locator.port) locator.port = port;
    return true;
  }
  bool fillMetatrafficUnicastLocator(Locator & locator, uint32_t port) const override
  {
    return fill(locator, port);
  }
  bool configureInitialPeerLocator(
    Locator & locator, const PortParameters & ports, uint32_t domain,
    LocatorList & locators) const override
  {
    if (!IsLocatorSupported(locator)) return false;
    if (!locator.port) locator.port = ports.getMulticastPort(domain);
    locators.push_back(locator);
    return true;
  }
  bool fillUnicastLocator(Locator & locator, uint32_t port) const override
  {
    return fill(locator, port);
  }
  uint32_t max_recv_buffer_size() const override { return descriptor_.maxMessageSize; }
};
}  // namespace

Descriptor::Descriptor(std::string path, unsigned role)
: TransportDescriptorInterface(max_packet_bytes, 1), socket(std::move(path)), side(role)
{
  if (side != 1 && side != 2) throw std::invalid_argument("DDS side must be host or guest");
}
eprosima::fastdds::rtps::TransportInterface * Descriptor::create_transport() const
{
  return new Transport(*this);
}
}  // namespace chimaera_dds
