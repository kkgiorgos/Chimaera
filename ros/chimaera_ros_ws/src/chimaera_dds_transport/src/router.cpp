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
  };
  std::string path;
  unsigned side;
  std::size_t limit, queued{};
  int server{-1}, wake{-1};
  std::atomic<bool> stopped{false};
  mutable std::mutex mutex;
  std::map<int, Client> clients;
  std::deque<Packet> outgoing;
  std::map<std::pair<std::string, bool>, std::map<uint32_t, std::size_t>> endpoints;
  std::string failure;
  std::function<void()> notify;
  std::thread worker;

  Impl(std::string socket, unsigned role, std::size_t bytes)
  : path(std::move(socket)), side(role), limit(bytes)
  {
    if ((side != 1 && side != 2) || limit < 2 * max_packet_bytes)
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
    uint64_t value = 1;
    const auto result = ::write(wake, &value, sizeof(value));
    (void)result;  // EAGAIN means a wakeup is already pending.
  }
  void reserve(std::size_t size)
  {
    if (size > limit - queued) throw std::runtime_error("DDS runtime queue capacity exceeded");
    queued += size;
  }
  void forward(const Packet & packet, int sender = -1)
  {
    const auto destination = wire::locator(packet.data() + 28);
    if (destination.address[0] && destination.address[0] != side) return;
    for (auto & [fd, client] : clients) {
      if (fd == sender) continue;
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
  void accept_packet(int fd, const Packet & packet)
  {
    const auto source = wire::locator(packet.data() + 8);
    const auto destination = wire::locator(packet.data() + 28);
    if (source.address[0] != side || destination.address[0] > 2)
      throw std::runtime_error("invalid DDS client locator role");
    auto & client = clients.at(fd);
    client.process = wire::read32(source.address + 4);
    if (packet.data()[5] == wire::open) {
      if (packet.size() != wire::header_bytes || client.channels.size() >= 16)
        throw std::runtime_error("invalid DDS channel registration");
      client.channels.push_back(destination);
      client.transport = true;
    } else if (packet.data()[5] == wire::close) {
      client.channels.erase(
        std::remove(client.channels.begin(), client.channels.end(), destination),
        client.channels.end());
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
      if (
        packet.size() < wire::header_bytes + 20 ||
        std::memcmp(packet.data() + wire::header_bytes, "RTPS", 4))
        throw std::runtime_error("invalid RTPS packet");
      forward(packet, fd);
      if (!destination.address[0] || destination.address[0] != side) {
        reserve(packet.size());
        outgoing.push_back(packet);
      }
    }
  }
  bool service(int fd, short events)
  {
    auto & client = clients.at(fd);
    bool alive = true;
    if (events & (POLLIN | POLLHUP)) {
      while (true) {
        // Read exactly one header, validate its length before allocating, then
        // receive directly into the allocation shared by all downstream queues.
        const auto target = client.input.size() ? client.input.size() : wire::header_bytes;
        auto * bytes = client.input.size() ? client.input.data() : client.header.data();
        const auto size = ::recv(fd, bytes + client.filled, target - client.filled, 0);
        if (size < 0 && errno == EINTR) continue;
        if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (size <= 0) {
          alive = false;
          break;
        }
        client.filled += std::size_t(size);
        if (!client.input.size() && client.filled == wire::header_bytes) {
          const auto length = wire::length(client.header.data(), wire::header_bytes);
          client.input = Packet::for_overwrite(length);
          std::memcpy(client.input.data(), client.header.data(), wire::header_bytes);
        }
        if (client.input.size() && client.filled == client.input.size()) {
          accept_packet(fd, client.input);
          client.input = {};
          client.filled = 0;
        }
      }
    }
    if (events & POLLOUT)
      while (!client.output.empty()) {
        auto & packet = client.output.front();
        const auto size =
          ::send(fd, packet.data() + client.offset, packet.size() - client.offset, MSG_NOSIGNAL);
        if (size < 0 && errno == EINTR) continue;
        if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (size <= 0) {
          alive = false;
          break;
        }
        client.offset += size;
        if (client.offset == packet.size()) {
          queued -= packet.size();
          client.output.pop_front();
          client.offset = 0;
        }
      }
    return alive && !(events & (POLLERR | POLLNVAL));
  }
  void run()
  {
    try {
      while (!stopped) {
        std::vector<pollfd> descriptors{{server, POLLIN, 0}, {wake, POLLIN, 0}};
        {
          std::lock_guard lock(mutex);
          for (const auto & [fd, client] : clients)
            descriptors.push_back({fd, short(POLLIN | (client.output.empty() ? 0 : POLLOUT)), 0});
        }
        if (::poll(descriptors.data(), descriptors.size(), -1) < 0) {
          if (errno == EINTR) continue;
          throw std::runtime_error("DDS runtime poll failed");
        }
        std::function<void()> notification;
        {
          std::lock_guard lock(mutex);
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
            const auto fd = descriptors[i].fd;
            if (descriptors[i].revents && !service(fd, descriptors[i].revents)) {
              const auto process = clients.at(fd).process;
              const bool transport = clients.at(fd).transport;
              for (auto & packet : clients.at(fd).output) queued -= packet.size();
              ::close(fd);
              clients.erase(fd);
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
          if (!outgoing.empty()) notification = notify;
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
  if (impl_->outgoing.empty()) return std::nullopt;
  auto packet = std::move(impl_->outgoing.front());
  impl_->outgoing.pop_front();
  impl_->queued -= packet.size();
  return packet;
}
void Router::receive(Packet packet)
{
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
  impl_->forward(packet);
  impl_->signal();
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
  return impl_->outgoing.size();
}
}  // namespace chimaera_dds
