#include <fcntl.h>
#include <poll.h>
#include <sys/eventfd.h>

#include <algorithm>
#include <atomic>
#include <chimaera_dds_transport/router.hpp>
#include <deque>
#include <map>
#include <mutex>
#include <thread>

#include "protocol.hpp"

namespace chimaera_dds
{
struct Router::Impl
{
  static constexpr std::size_t service_byte_budget = 256 * 1024;
  static constexpr std::size_t service_record_budget = 64;
  struct Client
  {
    std::vector<wire::Locator> channels;
    std::array<uint8_t, wire::header_bytes> header{};
    Packet input;
    std::size_t filled{};
    std::deque<Packet> output;
    std::size_t offset{};
    uint32_t process{};
    bool transport{};
    bool failed{};
    bool blocked{};
  };
  struct Lane
  {
    std::deque<Packet> outgoing;
    std::size_t local_bytes{}, remote_bytes{};
  };
  std::string path;
  unsigned side;
  const bool bounded_service;
  bool metadata_only;
  std::size_t limit, queued{};
  int server{-1}, wake{-1};
  std::atomic<bool> stopped{false};
  mutable std::mutex mutex;
  std::map<int, Client> clients;
  std::deque<Packet> outgoing;
  std::array<Lane, 2> lanes;
  bool retry_inputs{};
  int retry_after{-1};
  std::map<std::pair<std::string, bool>, std::map<uint32_t, std::size_t>> endpoints;
  std::string failure;
  std::function<void()> notify;
  std::thread worker;

  Impl(std::string socket, unsigned role, std::size_t bytes)
  : path(std::move(socket)),
    side(role),
    bounded_service(m5ops_backend()),
    metadata_only(role == 2 && bounded_service),
    limit(bytes)
  {
    const auto minimum =
      m5ops_backend() ? 2 * (m5ops_max_packet_bytes + wire::header_bytes) : 2 * max_packet_bytes;
    if ((side != 1 && side != 2) || limit < minimum)
      throw std::invalid_argument("invalid DDS router side or capacity");
    const auto address = wire::address(path);
    server = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (server < 0) throw std::runtime_error("cannot create DDS runtime socket");
    if (::bind(server, reinterpret_cast<const sockaddr *>(&address), sizeof(address))) {
      ::close(server);
      throw std::runtime_error("cannot bind DDS runtime socket: " + path);
    }
    wake = ::eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    if (::listen(server, 128) || wake < 0) {
      ::close(server);
      if (wake >= 0) ::close(wake);
      ::unlink(path.c_str());
      throw std::runtime_error("cannot start DDS runtime");
    }
    worker = std::thread([this] { run(); });
  }
  ~Impl()
  {
    stopped = true;
    signal();
    if (worker.joinable()) worker.join();
    for (auto & item : clients) ::close(item.first);
    ::close(server);
    ::close(wake);
    ::unlink(path.c_str());
  }
  void signal() const
  {
    const uint64_t value = 1;
    ssize_t result;
    do {
      result = ::write(wake, &value, sizeof(value));
    } while (result < 0 && errno == EINTR);
    // EAGAIN means a persistent wakeup is already pending.
  }
  void reserve(std::size_t size)
  {
    if (size > limit - queued) throw std::runtime_error("DDS runtime queue capacity exceeded");
    queued += size;
  }
  static unsigned lane_index(const Packet & packet)
  {
    return (wire::read32(packet.data() + 28) & bulk_port_bit) ? 1U : 0U;
  }
  bool has_outgoing() const
  {
    return bounded_service ? !lanes[0].outgoing.empty() || !lanes[1].outgoing.empty()
                           : !outgoing.empty();
  }
  std::vector<Client *> recipients(const Packet & packet, int sender)
  {
    std::vector<Client *> result;
    const auto destination = wire::locator(packet.data() + 28);
    if (destination.address[0] && destination.address[0] != side) return result;
    for (auto & [fd, client] : clients) {
      if (fd == sender || client.failed) continue;
      for (const auto & channel : client.channels) {
        if (channel.port != destination.port) continue;
        if (destination.address[0] && std::memcmp(channel.address, destination.address, 16))
          continue;
        result.push_back(&client);
        break;
      }
    }
    return result;
  }
  bool try_enqueue(const Packet & packet, int sender, bool remote)
  {
    auto & lane = lanes[lane_index(packet)];
    const auto targets = recipients(packet, sender);
    // Check the entire fanout and independent remote budget before any queue
    // mutation. Division avoids overflow when accounting for many recipients.
    if (
      targets.size() > (limit - lane.local_bytes) / packet.size() ||
      (remote && packet.size() > limit - lane.remote_bytes))
      return false;
    for (auto * target : targets) {
      target->output.push_back(packet);
      lane.local_bytes += packet.size();
    }
    if (remote) {
      lane.outgoing.push_back(packet);
      lane.remote_bytes += packet.size();
    }
    return true;
  }
  void release_local(const Packet & packet)
  {
    if (bounded_service) {
      lanes[lane_index(packet)].local_bytes -= packet.size();
      retry_inputs = true;
    } else {
      queued -= packet.size();
    }
  }
  void forward(const Packet & packet, int sender = -1)
  {
    const auto destination = wire::locator(packet.data() + 28);
    if (destination.address[0] && destination.address[0] != side) return;
    for (auto & [fd, client] : clients) {
      if (fd == sender || client.failed) continue;
      for (const auto & channel : client.channels) {
        if (channel.port != destination.port) continue;
        if (destination.address[0] && std::memcmp(channel.address, destination.address, 16))
          continue;
        reserve(packet.size());
        client.output.push_back(packet);
        break;
      }
    }
  }
  bool accept_packet(int fd, const Packet & packet)
  {
    const auto source = wire::locator(packet.data() + 8);
    const auto destination = wire::locator(packet.data() + 28);
    if (source.address[0] != side || destination.address[0] > 2)
      throw std::runtime_error("invalid DDS client locator role");
    auto & client = clients.at(fd);
    client.process = wire::read32(source.address + 4);
    if (packet.data()[5] == wire::open) {
      if (
        packet.size() != wire::header_bytes ||
        client.channels.size() >= (m5ops_backend() ? 4096U : 16U))
        throw std::runtime_error("invalid DDS channel registration");
      client.channels.push_back(destination);
      client.transport = true;
    } else if (packet.data()[5] == wire::close) {
      client.channels.erase(
        std::remove(client.channels.begin(), client.channels.end(), destination),
        client.channels.end());
      if (bounded_service) retry_inputs = true;
    } else if (packet.data()[5] == wire::endpoint) {
      if (
        packet.size() < wire::header_bytes + 2 || packet.size() > wire::header_bytes + 258 ||
        (packet.data()[wire::header_bytes] & 0x7e))
        throw std::runtime_error("invalid DDS endpoint registration");
      const auto key = std::make_pair(
        std::string(packet.data() + wire::header_bytes + 1, packet.data() + packet.size()),
        bool(packet.data()[wire::header_bytes] & 1));
      if (packet.data()[wire::header_bytes] & 0x80) {
        auto found = endpoints.find(key);
        if (found != endpoints.end()) {
          auto owner = found->second.find(client.process);
          if (owner != found->second.end() && !--owner->second) found->second.erase(owner);
          if (found->second.empty()) endpoints.erase(found);
        }
      } else {
        ++endpoints[key][client.process];
      }
    } else {
      if (metadata_only) throw std::runtime_error("payload on metadata-only guest DDS router");
      if (
        packet.size() < wire::header_bytes + 20 ||
        std::memcmp(packet.data() + wire::header_bytes, "RTPS", 4))
        throw std::runtime_error("invalid RTPS packet");
      const bool remote = !destination.address[0] || destination.address[0] != side;
      if (bounded_service) return try_enqueue(packet, fd, remote);
      forward(packet, fd);
      if (remote) {
        reserve(packet.size());
        outgoing.push_back(packet);
      }
    }
    return true;
  }
  void retry_blocked_inputs()
  {
    if (!retry_inputs) return;
    retry_inputs = false;
    // Each parked client holds exactly one complete frame. Visit it once per
    // credit-change pass; a still-full lane must not keep the worker awake.
    // Start after the last successful retry so low-numbered fds cannot always
    // consume newly released credit ahead of another parked producer.
    const auto first = clients.upper_bound(retry_after);
    auto retry = [&](auto begin, auto end) {
      for (auto it = begin; it != end; ++it) {
        auto & [fd, client] = *it;
        if (!client.blocked || client.failed) continue;
        if (!accept_packet(fd, client.input)) continue;
        client.input = {};
        client.filled = 0;
        client.blocked = false;
        retry_after = fd;
      }
    };
    retry(first, clients.end());
    retry(clients.begin(), first);
  }
  bool flush(int fd, Client & client, std::size_t budget)
  {
    // Both the worker and receive() use this path. Yield between bounded
    // output passes while retaining the front packet's owner and byte offset.
    if (bounded_service) budget = std::min(budget, service_byte_budget);
    std::size_t completed = 0;
    while (!client.output.empty() && budget &&
           (!bounded_service || completed < service_record_budget)) {
      auto & packet = client.output.front();
      const auto size = ::send(
        fd, packet.data() + client.offset, std::min(packet.size() - client.offset, budget),
        MSG_NOSIGNAL);
      if (size < 0 && errno == EINTR) continue;
      if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
      if (size <= 0) {
        client.failed = true;
        return false;
      }
      client.offset += size;
      budget -= size;
      if (client.offset == packet.size()) {
        release_local(packet);
        client.output.pop_front();
        client.offset = 0;
        ++completed;
      }
    }
    return true;
  }
  bool service(int fd, short events)
  {
    auto & client = clients.at(fd);
    bool alive = true;
    if (!client.blocked && (events & (POLLIN | POLLHUP))) {
      std::size_t received = 0, completed = 0;
      while (!bounded_service ||
             (received < service_byte_budget && completed < service_record_budget)) {
        // Read exactly one header, validate its length before allocating, then
        // receive directly into the allocation shared by all downstream queues.
        const auto target = client.input.size() ? client.input.size() : wire::header_bytes;
        auto * bytes = client.input.size() ? client.input.data() : client.header.data();
        const auto remaining = target - client.filled;
        const auto count =
          bounded_service ? std::min(remaining, service_byte_budget - received) : remaining;
        const auto size = ::recv(fd, bytes + client.filled, count, 0);
        if (size < 0 && errno == EINTR) continue;
        if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (size <= 0) {
          alive = false;
          break;
        }
        client.filled += std::size_t(size);
        received += std::size_t(size);
        if (!client.input.size() && client.filled == wire::header_bytes) {
          const auto length = wire::length(client.header.data(), wire::header_bytes);
          client.input = Packet::for_overwrite(length);
          std::memcpy(client.input.data(), client.header.data(), wire::header_bytes);
        }
        if (client.input.size() && client.filled == client.input.size()) {
          if (!accept_packet(fd, client.input)) {
            client.blocked = true;
            break;
          }
          client.input = {};
          client.filled = 0;
          ++completed;
        }
      }
      // A budget stop retains filled/input for the next level-triggered poll,
      // including POLLHUP with unread bytes. Only recv(0) finishes the stream.
    }
    // A parked input cannot read EOF yet. HUP must still retire a disconnected
    // output peer rather than keeping its unsendable queue in a hot poll loop.
    if ((events & POLLOUT) || (client.blocked && (events & POLLHUP)))
      alive = flush(fd, client, limit) && alive;
    return alive && !(events & (POLLERR | POLLNVAL));
  }
  void run()
  {
    try {
      while (!stopped) {
        std::vector<pollfd> descriptors{{server, POLLIN, 0}, {wake, POLLIN, 0}};
        {
          std::lock_guard lock(mutex);
          for (const auto & [fd, client] : clients) {
            // events=0 still reports HUP. Omit a parked producer entirely
            // when it has no output, then wake/reconsider it on credit release.
            if (client.blocked && client.output.empty() && !client.failed) continue;
            descriptors.push_back(
              {fd, short((client.blocked ? 0 : POLLIN) | (client.output.empty() ? 0 : POLLOUT)),
               0});
          }
        }
        if (::poll(descriptors.data(), descriptors.size(), -1) < 0) {
          if (errno == EINTR) continue;
          throw std::runtime_error("DDS runtime poll failed");
        }
        std::function<void()> notification;
        {
          std::lock_guard lock(mutex);
          bool credits_changed = false;
          auto reconsider_inputs = [&] {
            if (!retry_inputs) return;
            credits_changed = true;
            retry_blocked_inputs();
          };
          if (descriptors[1].revents) {
            uint64_t value;
            const auto result = ::read(wake, &value, sizeof(value));
            (void)result;
          }
          if (descriptors[0].revents)
            while (true) {
              const auto fd = ::accept4(server, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
              if (fd < 0) break;
              if (clients.size() >= 128) {
                ::close(fd);
                throw std::runtime_error("too many DDS clients");
              }
              clients.emplace(fd, Client{});
            }
          for (std::size_t i = 2; i < descriptors.size(); ++i) {
            // Give parked producers a fair chance at returned credit before
            // a newly readable client can consume it in this same pass.
            reconsider_inputs();
            const auto fd = descriptors[i].fd;
            if (
              clients.at(fd).failed ||
              (descriptors[i].revents && !service(fd, descriptors[i].revents))) {
              const auto process = clients.at(fd).process;
              const bool transport = clients.at(fd).transport;
              for (auto & packet : clients.at(fd).output) release_local(packet);
              ::close(fd);
              clients.erase(fd);
              if (bounded_service) retry_inputs = true;
              if (
                transport &&
                std::none_of(clients.begin(), clients.end(), [process](const auto & item) {
                  return item.second.transport && item.second.process == process;
                })) {
                for (auto it = endpoints.begin(); it != endpoints.end();) {
                  it->second.erase(process);
                  if (it->second.empty())
                    it = endpoints.erase(it);
                  else
                    ++it;
                }
              }
            }
          }
          reconsider_inputs();
          // Also let the coordinator retry admitted input it still owns when
          // local recipients release credit, even if no outgoing packet exists.
          if (has_outgoing() || credits_changed) notification = notify;
        }
        if (notification) notification();
      }
    } catch (const std::exception & error) {
      std::function<void()> notification;
      {
        std::lock_guard lock(mutex);
        failure = error.what();
        notification = notify;
      }
      if (notification) notification();
    }
  }
};

Router::Router(std::string socket, unsigned side, std::size_t bytes)
: impl_(std::make_unique<Impl>(std::move(socket), side, bytes))
{
}
Router::~Router() = default;
void Router::check() const
{
  std::lock_guard lock(impl_->mutex);
  if (!impl_->failure.empty()) throw std::runtime_error(impl_->failure);
}
std::optional<Packet> Router::take()
{
  std::lock_guard lock(impl_->mutex);
  if (!impl_->failure.empty()) throw std::runtime_error(impl_->failure);
  if (impl_->bounded_service) {
    for (auto & lane : impl_->lanes) {
      if (lane.outgoing.empty()) continue;
      auto packet = std::move(lane.outgoing.front());
      lane.outgoing.pop_front();
      lane.remote_bytes -= packet.size();
      impl_->retry_inputs = true;
      impl_->signal();
      return packet;
    }
    return std::nullopt;
  }
  if (impl_->outgoing.empty()) return std::nullopt;
  auto packet = std::move(impl_->outgoing.front());
  impl_->outgoing.pop_front();
  impl_->queued -= packet.size();
  return packet;
}
std::size_t Router::transfer_outgoing(const std::function<bool(Packet)> & receiver)
{
  std::lock_guard lock(impl_->mutex);
  if (!impl_->failure.empty()) throw std::runtime_error(impl_->failure);
  std::size_t accepted = 0;
  auto transfer = [&](std::deque<Packet> & queue, Impl::Lane * lane) {
    while (!queue.empty()) {
      const auto size = queue.front().size();
      // Packet is a shared-storage copy. Do not release this queue's original
      // owner/credit until the downstream stage confirms it accepted the copy.
      if (!receiver(queue.front())) break;
      queue.pop_front();
      if (lane) {
        lane->remote_bytes -= size;
        impl_->retry_inputs = true;
      } else {
        impl_->queued -= size;
      }
      ++accepted;
    }
  };
  try {
    if (impl_->bounded_service) {
      // Refusal stops only its lane. Control cannot be held behind bulk.
      for (auto & lane : impl_->lanes) transfer(lane.outgoing, &lane);
    } else {
      transfer(impl_->outgoing, nullptr);
    }
  } catch (...) {
    if (impl_->retry_inputs) impl_->signal();
    throw;
  }
  if (impl_->retry_inputs) impl_->signal();
  return accepted;
}
bool Router::try_receive(Packet packet)
{
  if (impl_->metadata_only)
    throw std::runtime_error("guest m5ops DDS payload must be admitted to direct endpoints");
  const auto * bytes = packet.data();
  const auto size = packet.size();
  if (size < wire::header_bytes || wire::length(bytes, size) != size || bytes[5] != wire::data)
    throw std::runtime_error("invalid cross-simulator DDS packet");
  const auto source = wire::locator(bytes + 8), destination = wire::locator(bytes + 28);
  if (
    source.address[0] != 3 - impl_->side ||
    (destination.address[0] && destination.address[0] != impl_->side) ||
    size < wire::header_bytes + 20 || std::memcmp(bytes + wire::header_bytes, "RTPS", 4))
    throw std::runtime_error("invalid cross-simulator DDS source, destination or payload");
  std::lock_guard lock(impl_->mutex);
  if (!impl_->failure.empty()) throw std::runtime_error(impl_->failure);
  if (impl_->bounded_service) {
    if (!impl_->try_enqueue(packet, -1, false)) return false;
  } else {
    impl_->forward(packet);
  }
  bool pending = false;
  // The packet has already crossed its simulation boundary. Try the local
  // nonblocking sockets now rather than scheduling the worker before sending.
  // Bound each recipient's work; partial writes keep their FIFO offset/owner.
  for (auto & [fd, client] : impl_->clients) {
    if (!client.failed)
      impl_->flush(fd, client, configured_max_packet_bytes() + wire::header_bytes);
    pending = pending || client.failed || !client.output.empty();
  }
  // Only the worker closes clients, since it may have an in-flight poll snapshot.
  if (pending || impl_->retry_inputs) impl_->signal();
  return true;
}
void Router::receive(Packet packet)
{
  if (!try_receive(std::move(packet)))
    throw std::runtime_error("DDS runtime local fanout capacity exceeded");
}
void Router::receive(const void * data, std::size_t size)
{
  if (size < wire::header_bytes || wire::length(static_cast<const uint8_t *>(data), size) != size)
    throw std::runtime_error("invalid cross-simulator DDS packet");
  auto packet = Packet::for_overwrite(size);
  std::memcpy(packet.data(), data, size);
  receive(std::move(packet));
}
bool Router::endpoint_ready(const std::string & topic, bool publisher) const
{
  std::lock_guard lock(impl_->mutex);
  return impl_->endpoints.count({topic, publisher});
}
void Router::set_notify(std::function<void()> notify)
{
  std::lock_guard lock(impl_->mutex);
  impl_->notify = std::move(notify);
}
std::size_t Router::pending() const
{
  std::lock_guard lock(impl_->mutex);
  if (impl_->bounded_service)
    return impl_->lanes[0].outgoing.size() + impl_->lanes[1].outgoing.size();
  return impl_->outgoing.size();
}
}  // namespace chimaera_dds
