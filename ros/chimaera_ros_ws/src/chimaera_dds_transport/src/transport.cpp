#include <fastdds/rtps/transport/TransportInterface.h>
#include <fastdds/rtps/transport/TransportReceiverInterface.h>
#include <poll.h>
#include <sys/eventfd.h>

#ifdef CHIMAERA_DDS_HAS_M5OPS
#include <m5_mmap.h>
#include <sys/mman.h>

#include <chimaera/m5_endpoint.hpp>
#endif

#include <atomic>
#include <chimaera_dds_transport/functional_copy.hpp>
#include <chimaera_dds_transport/packet.hpp>
#include <chimaera_dds_transport/trace.hpp>
#include <chimaera_dds_transport/transport.hpp>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <fastdds/rtps/common/LocatorSelectorEntry.hpp>
#include <map>
#include <mutex>
#include <system_error>
#include <thread>

#include "latest_sample.hpp"
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
  std::mutex send_mutex_, channels_mutex_, stop_mutex_;
  std::map<unsigned, std::shared_ptr<Channel>> channels_;
  std::thread reader_;
  struct Lane
  {
#ifdef CHIMAERA_DDS_HAS_M5OPS
    std::unique_ptr<chimaera::M5Endpoint> endpoint;
#endif
    int fd{-1};
    std::mutex send_mutex;
    std::mutex channels_mutex;
    int cancel{-1};
    std::thread reader;
    // Host-only FIFO. Account for the worker's current frame as well as the
    // queue; removing a frame for a blocking write does not return its credit.
    static constexpr std::size_t tx_byte_limit = 64 * 1024 * 1024;
    static constexpr std::size_t tx_record_limit = 128;
    std::deque<Packet> tx;
    std::size_t tx_bytes{}, tx_records{};
    std::condition_variable tx_ready;
    std::thread writer;
    uint64_t coalesced{};
    ~Lane()
    {
      if (cancel >= 0) ::close(cancel);
    }
  };
  std::array<Lane, 2> lanes_;
#ifdef CHIMAERA_DDS_HAS_M5OPS
  static_assert(bulk_port_bit == chimaera::m5_bulk_port_bit);
  static_assert(application_port_bit == chimaera::m5_application_port_bit);
#endif
  static unsigned lane_index(uint32_t port) { return (port & bulk_port_bit) ? 1U : 0U; }
  Lane & lane(uint32_t port) { return lanes_[lane_index(port)]; }

  bool direct() const { return descriptor_.m5ops && descriptor_.side == 2; }
  bool split_sockets() const { return descriptor_.m5ops && descriptor_.side == 1; }
  std::unique_lock<std::mutex> channel_registration_lock(uint32_t port)
  {
    if (descriptor_.m5ops) return std::unique_lock(lane(port).channels_mutex);
    return {};
  }
  void stop()
  {
    std::lock_guard lock(stop_mutex_);
    if (stopped_.exchange(true)) return;
    if (fd_ >= 0) ::shutdown(fd_, SHUT_RDWR);
    for (auto & service : lanes_) {
      if (service.fd >= 0) ::shutdown(service.fd, SHUT_RDWR);
      if (split_sockets()) {
        // Synchronize with the wait predicate even when the FIFO is empty.
        // No host socket write or call to stop() holds this mutex.
        std::lock_guard send_lock(service.send_mutex);
        service.tx_ready.notify_all();
      }
      if (service.cancel < 0) continue;
      const uint64_t value = 1;
      ssize_t result;
      do {
        result = ::write(service.cancel, &value, sizeof(value));
      } while (result < 0 && errno == EINTR);
      // EAGAIN retains an already-pending, persistent cancellation notification.
    }
  }
  void deliver(const uint8_t * bytes, std::size_t size, uint64_t arrived, bool dispatch = true)
  {
    const auto destination = wire::locator(bytes + 28);
    std::shared_ptr<Channel> channel;
    {
      std::lock_guard lock(channels_mutex_);
      const auto found = channels_.find(destination.port);
      if (found != channels_.end()) channel = found->second;
    }
    if (!channel) return;
    if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
      std::fprintf(
        stderr, "Chimaera DDS receive: side=%u port=%u bytes=%zu\n", descriptor_.side,
        destination.port, size - wire::header_bytes);
    // Distinct topic ports keep image processing off a control channel's lock.
    {
      std::lock_guard lock(channel->mutex);
      if (channel->receiver && dispatch) {
        FunctionalCopyScope middleware_copies;
        channel->receiver->OnDataReceived(
          bytes + wire::header_bytes, uint32_t(size - wire::header_bytes), channel->locator,
          wire::locator(bytes + 8));
      }
    }
    trace::packet(
      "receive", bytes + wire::header_bytes, size - wire::header_bytes, arrived, destination.port);
  }
#ifdef CHIMAERA_DDS_HAS_M5OPS
  void read_lane(unsigned index)
  {
    auto & service = lanes_[index];
    try {
      while (!stopped_) {
        // Batches contain only packets already ready. The driver retains any
        // arrival between the empty check and poll; no periodic wakeup is needed.
        while (!stopped_) {
          auto messages = service.endpoint->take_batch();
          if (messages.empty()) break;
          // All loans are already present. DDS work for an earlier datagram in
          // this batch must not become a later datagram's transport delay.
          const auto arrived = trace::enabled() ? trace::now() : trace::unset;
          for (const auto & message : messages) {
            const auto * bytes = reinterpret_cast<const uint8_t *>(message.data());
            const auto size = message.size();
            if (
              size < wire::header_bytes + 20 || wire::length(bytes, size) != size ||
              bytes[5] != wire::data || std::memcmp(bytes + wire::header_bytes, "RTPS", 4))
              throw std::runtime_error("invalid direct DDS frame");
            const auto source = wire::locator(bytes + 8), destination = wire::locator(bytes + 28);
            if (
              (source.address[0] != 1 && source.address[0] != 2) ||
              (destination.address[0] && destination.address[0] != 2) ||
              lane_index(destination.port) != index)
              throw std::runtime_error("invalid direct DDS locator or lane");
          }
          const auto keep = retain_latest(messages, coalesce_latest);
          for (std::size_t i = 0; i < messages.size(); ++i) {
            // Every arrival remains traced, including superseded best-effort
            // state. Only DDS dispatch is skipped; loans are returned normally.
            service.coalesced += !keep[i];
            deliver(
              reinterpret_cast<const uint8_t *>(messages[i].data()), messages[i].size(), arrived,
              keep[i]);
          }
          // Batch owns every registered RX slot through all OnDataReceived
          // calls, then returns the complete group in one m5 operation.
        }
        if (stopped_) break;
        pollfd descriptors[2]{
          {service.endpoint->completion_fd(), POLLIN, 0},
          { service.cancel,
            POLLIN,
            0 }};
        const auto result = ::poll(descriptors, 2, -1);
        if (result < 0 && errno == EINTR) continue;
        if (result < 0) throw std::system_error(errno, std::generic_category(), "DDS lane poll");
        if (descriptors[1].revents || stopped_) break;
        if (descriptors[0].revents & (POLLERR | POLLHUP | POLLNVAL))
          throw std::runtime_error("DDS m5 endpoint closed or failed");
      }
    } catch (const std::exception & error) {
      std::fprintf(stderr, "Chimaera DDS lane %u receive: %s\n", index, error.what());
      stop();
    }
  }
  void init_direct()
  {
    // Participant discovery starts under KVM before the ROI, including runs
    // which later switch to detailed CPUs. Always use address operations.
    static std::once_flag mapping;
    std::call_once(mapping, [] {
      if (!m5_mem) {
        m5op_addr = 0xFFFF0000;
        map_m5_mem();
      }
      if (!m5_mem || m5_mem == MAP_FAILED)
        throw std::runtime_error("cannot map DDS m5 address operations");
      // The private libm5 mapping is process-lived: no participant teardown or
      // clock-plugin destructor can unmap it underneath another participant.
    });
    const auto device = std::getenv("CHIMAERA_M5_DEVICE");
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    for (unsigned index = 0; index < lanes_.size(); ++index) {
      auto & service = lanes_[index];
      service.cancel = ::eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
      if (service.cancel < 0)
        throw std::system_error(errno, std::generic_category(), "DDS lane cancellation");
      while (true) {
        try {
          service.endpoint = std::make_unique<chimaera::M5Endpoint>(
            index ? chimaera::M5Lane::bulk : chimaera::M5Lane::control,
            device && *device ? device : "/dev/chimaera-m5", chimaera::GuestM5Ops::address);
          break;
        } catch (const std::system_error & error) {
          const auto code = error.code().value();
          // The bridge may still be loading the notification module while ROS
          // creates its first participant. Retry only setup readiness, never
          // ABI errors, exhausted pools or payload/completion work.
          if (
            (code != ENOENT && code != ENODEV && code != ENXIO && code != EINTR) ||
            std::chrono::steady_clock::now() >= deadline)
            throw;
          std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
      }
    }
    for (unsigned index = 0; index < lanes_.size(); ++index)
      lanes_[index].reader = std::thread([this, index] { read_lane(index); });
  }
#endif

  bool enqueue_host(
    wire::Type type, const std::array<uint8_t, wire::header_bytes> & prefix,
    const wire::Locator & destination, const void * body, std::size_t size)
  {
    auto & service = lane(destination.port);
    const auto frame_size = prefix.size() + size;
    try {
      {
        std::lock_guard lock(service.send_mutex);
        if (stopped_) return false;
        if (
          service.tx_records < Lane::tx_record_limit &&
          frame_size <= Lane::tx_byte_limit - service.tx_bytes) {
          auto packet = Packet::for_overwrite(frame_size);
          std::memcpy(packet.data(), prefix.data(), prefix.size());
          if (size) std::memcpy(packet.data() + prefix.size(), body, size);
          service.tx.push_back(std::move(packet));
          service.tx_bytes += frame_size;
          ++service.tx_records;
          service.tx_ready.notify_one();
          return true;
        }
      }
    } catch (const std::exception & error) {
      std::fprintf(stderr, "Chimaera DDS enqueue: %s\n", error.what());
    }
    // Refused DATA is an unaccepted datagram. Fast DDS 2.6 ignores this false
    // result; reliable RTPS recovery is separate, and best effort may lose it.
    // Metadata cannot be dropped: disconnect instead of leaving a stale port
    // after a refused Close. OpenInputChannel rolls its local state back.
    if (type != wire::data) stop();
    return false;
  }
  void write_lane(unsigned index)
  {
    auto & service = lanes_[index];
    while (true) {
      Packet packet;
      {
        std::unique_lock lock(service.send_mutex);
        service.tx_ready.wait(lock, [&] { return stopped_ || !service.tx.empty(); });
        if (stopped_) return;
        packet = std::move(service.tx.front());
        service.tx.pop_front();
      }
      // Only this worker writes the host lane fd. The owned frame outlives
      // every partial write; no Fast DDS buffer survives its send callback.
      if (!wire::send_all(service.fd, packet.data(), packet.size())) {
        stop();
        return;
      }
      const auto size = packet.size();
      packet = {};
      {
        std::lock_guard lock(service.send_mutex);
        service.tx_bytes -= size;
        --service.tx_records;
      }
    }
  }
  bool send_packet_impl(
    wire::Type type, const wire::Locator & source, const wire::Locator & destination,
    const void * body = nullptr, std::size_t size = 0)
  {
    if (size > descriptor_.maxMessageSize) return false;
    const auto prefix = wire::header(type, source, destination, size);
    if (stopped_) return false;
    // Fast DDS holds its participant-wide send mutex during this callback.
    // Copy complete frames, including Open/Close, into the host lane FIFO;
    // waiting for router credit is exclusively the lane worker's job.
    if (split_sockets()) return enqueue_host(type, prefix, destination, body, size);
    if (direct() && type == wire::data) {
#ifdef CHIMAERA_DDS_HAS_M5OPS
      auto & service = lane(destination.port);
      std::lock_guard lock(service.send_mutex);
      if (stopped_) return false;
      try {
        // The endpoint consumes both borrowed spans before returning. Refusal
        // is an unaccepted datagram, not an SDK retry request (see host FIFO).
        return service.endpoint->send(
          std::as_bytes(std::span(prefix)), {static_cast<const std::byte *>(body), size});
      } catch (const std::exception & error) {
        std::fprintf(stderr, "Chimaera DDS send: %s\n", error.what());
        stop();
        return false;
      }
#else
      return false;
#endif
    }
    // Legacy traffic and direct guest registration metadata remain synchronous.
    std::lock_guard lock(send_mutex_);
    if (stopped_) return false;
    if (wire::send_frame(fd_, prefix, body, size)) return true;
    // A timed-out stream write may contain only part of a frame. Retire the
    // connection so another packet cannot be mistaken for its remaining bytes.
    stop();
    return false;
  }
  bool send_packet(
    wire::Type type, const wire::Locator & source, const wire::Locator & destination,
    const void * body = nullptr, std::size_t size = 0)
  {
    if (type != wire::data || !trace::enabled())
      return send_packet_impl(type, source, destination, body, size);
    const auto started = trace::now();
    const auto accepted = send_packet_impl(type, source, destination, body, size);
    if (accepted)
      trace::packet("send", static_cast<const uint8_t *>(body), size, started, destination.port);
    return accepted;
  }
  void read_socket(int socket, int service_index = -1)
  {
    try {
      std::array<uint8_t, wire::header_bytes> prefix;
      Packet packet;
      while (!stopped_ && wire::receive_all(socket, prefix.data(), prefix.size())) {
        const auto size = wire::length(prefix.data(), prefix.size());
        if (direct()) throw std::runtime_error("payload on metadata-only DDS connection");
        if (packet.size() < size) packet = Packet::for_overwrite(size);
        std::memcpy(packet.data(), prefix.data(), prefix.size());
        if (!wire::receive_all(
              socket, packet.data() + wire::header_bytes, size - wire::header_bytes))
          break;
        const auto arrived = trace::enabled() ? trace::now() : trace::unset;
        if (
          service_index >= 0 &&
          lane_index(wire::locator(packet.data() + 28).port) != unsigned(service_index))
          throw std::runtime_error("DDS socket received a packet on the wrong lane");
        deliver(packet.data(), size, arrived);
      }
    } catch (const std::exception & error) {
      std::fprintf(stderr, "Chimaera DDS receive: %s\n", error.what());
    }
    stop();
  }
  int connect_socket()
  {
    const auto address = wire::address(descriptor_.socket);
    const auto socket = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (socket < 0)
      throw std::system_error(errno, std::generic_category(), "create DDS runtime socket");
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    while (::connect(socket, reinterpret_cast<const sockaddr *>(&address), sizeof(address))) {
      const auto error = errno;
      if (
        (error != ENOENT && error != ECONNREFUSED && error != EINTR) ||
        std::chrono::steady_clock::now() >= deadline) {
        ::close(socket);
        throw std::system_error(error, std::generic_category(), "connect DDS runtime socket");
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    // A host bulk producer may wait for bounded router/controller credits.
    // Timing simulation can keep those credits occupied for more than five
    // wall seconds. Its lane worker retains the owned frame until credit
    // returns; shutdown() interrupts the blocking send.
    if (!split_sockets()) {
      timeval timeout{5, 0};
      ::setsockopt(socket, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    }
    return socket;
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
  // Serialize port removal with a reopen on the same lane. Release that lock
  // before waiting for callbacks: discovery may open another channel itself.
  bool close_input_channel(const Locator & locator, std::unique_lock<std::mutex> & registration)
  {
    std::shared_ptr<Channel> channel;
    {
      std::lock_guard lock(channels_mutex_);
      const auto found = channels_.find(locator.port);
      if (found == channels_.end()) return false;
      channel = found->second;
      channels_.erase(found);
    }
    bool success = true;
#ifdef CHIMAERA_DDS_HAS_M5OPS
    if (direct()) {
      try {
        auto & service = lane(locator.port);
        std::lock_guard lock(service.send_mutex);
        if (service.endpoint) service.endpoint->close_channel(locator.port);
      } catch (const std::exception & error) {
        std::fprintf(stderr, "Chimaera DDS close channel: %s\n", error.what());
        stop();
        success = false;
      }
    }
#endif
    success = send_packet(wire::close, wire::local(descriptor_.side), locator) && success;
    if (registration.owns_lock()) registration.unlock();
    {
      std::lock_guard lock(channel->mutex);
      channel->receiver = nullptr;
    }
    return success;
  }

public:
  explicit Transport(const Descriptor & descriptor)
  : TransportInterface(transport_kind), descriptor_(descriptor)
  {
  }
  ~Transport() override { shutdown(); }
  bool init(const PropertyPolicy *) override
  {
#ifndef CHIMAERA_DDS_HAS_M5OPS
    if (direct()) {
      std::fprintf(stderr, "Chimaera DDS built without direct m5ops support\n");
      return false;
    }
#endif
    try {
      if (split_sockets()) {
        // Publish all fds before starting any thread that can call stop().
        for (auto & service : lanes_) service.fd = connect_socket();
        for (unsigned index = 0; index < lanes_.size(); ++index)
          lanes_[index].writer = std::thread([this, index] { write_lane(index); });
        for (unsigned index = 0; index < lanes_.size(); ++index)
          lanes_[index].reader =
            std::thread([this, index] { read_socket(lanes_[index].fd, int(index)); });
      } else {
        fd_ = connect_socket();
#ifdef CHIMAERA_DDS_HAS_M5OPS
        if (direct()) init_direct();
#endif
        reader_ = std::thread([this] { read_socket(fd_); });
      }
      if (stopped_) throw std::runtime_error("DDS transport stopped during initialization");
    } catch (const std::exception & error) {
      std::fprintf(stderr, "Chimaera DDS initialization: %s\n", error.what());
      shutdown();
      return false;
    }
    if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
      std::fprintf(stderr, "Chimaera DDS connected: side=%u\n", descriptor_.side);
    return true;
  }
  void shutdown() override
  {
    stop();
    if (reader_.joinable()) reader_.join();
    for (auto & service : lanes_)
      if (service.reader.joinable()) service.reader.join();
    for (unsigned index = 0; index < lanes_.size(); ++index)
      if (lanes_[index].coalesced)
        std::fprintf(
          stderr, "Chimaera DDS lane %u: coalesced %llu superseded state samples\n", index,
          static_cast<unsigned long long>(lanes_[index].coalesced));
    for (auto & service : lanes_)
      if (service.writer.joinable()) service.writer.join();
    for (auto & service : lanes_) {
      std::lock_guard lock(service.send_mutex);
      service.tx.clear();
      service.tx_bytes = service.tx_records = 0;
#ifdef CHIMAERA_DDS_HAS_M5OPS
      service.endpoint.reset();
#endif
      if (service.fd >= 0) {
        ::close(service.fd);
        service.fd = -1;
      }
    }
    std::lock_guard lock(send_mutex_);
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
    if (IsLocatorSupported(remote))
      return wire::local(descriptor_.side, descriptor_.m5ops ? remote.port : 0);
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
    auto registration = channel_registration_lock(locator.port);
    auto channel = std::make_shared<Channel>();
    channel->receiver = receiver;
    channel->locator = locator;
    {
      std::lock_guard lock(channels_mutex_);
      if (channels_.count(locator.port)) return false;
      channels_[locator.port] = channel;
    }
    try {
#ifdef CHIMAERA_DDS_HAS_M5OPS
      if (direct()) {
        auto & service = lane(locator.port);
        std::lock_guard lock(service.send_mutex);
        if (stopped_ || !service.endpoint) throw std::runtime_error("DDS m5 endpoint is stopped");
        service.endpoint->open_channel(locator.port);
      }
#endif
      if (send_packet(wire::open, wire::local(descriptor_.side), locator)) return true;
    } catch (const std::exception & error) {
      std::fprintf(stderr, "Chimaera DDS open channel: %s\n", error.what());
    }
    // Roll back both registrations if either the device or metadata IPC fails.
    close_input_channel(locator, registration);
    return false;
  }
  bool CloseInputChannel(const Locator & locator) override
  {
    if (!IsLocatorSupported(locator)) return false;
    auto registration = channel_registration_lock(locator.port);
    return close_input_channel(locator, registration);
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
      bool added = false;
      auto select = [&](const Locator & locator, auto & indices, std::size_t i) {
        bool present = false;
        selector.for_each([&](const Locator & previous) { present |= previous == locator; });
        if (!present) {
          indices.push_back(i);
          added = true;
        }
        // A previously selected participant/port also serves this reader.
        // Do not fall back to another locator and send the same RTPS datagram
        // twice when multiple composed nodes subscribe to one routed topic.
        selected = true;
      };
      for (std::size_t i = 0; i < entry.unicast.size(); ++i) {
        if (IsLocatorSupported(entry.unicast[i])) {
          select(entry.unicast[i], entry.state.unicast, i);
          break;
        }
      }
      if (!selected)
        for (std::size_t i = 0; i < entry.multicast.size(); ++i) {
          if (IsLocatorSupported(entry.multicast[i])) {
            select(entry.multicast[i], entry.state.multicast, i);
            break;
          }
        }
      if (added) selector.select(index);
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
: TransportDescriptorInterface(configured_max_packet_bytes(), 1),
  socket(std::move(path)),
  side(role),
  m5ops(m5ops_backend())
{
  if (side != 1 && side != 2) throw std::invalid_argument("DDS side must be host or guest");
}
eprosima::fastdds::rtps::TransportInterface * Descriptor::create_transport() const
{
  return new Transport(*this);
}
}  // namespace chimaera_dds
