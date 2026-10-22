#include <dlfcn.h>
#include <poll.h>

#include <atomic>
#include <chimaera_dds_transport/router.hpp>
#include <chrono>
#include <filesystem>
#include <functional>
#include <iostream>
#include <thread>

#include "protocol.hpp"

using namespace std::chrono_literals;
using chimaera_dds::Router;
namespace wire = chimaera_dds::wire;

// Pause the router after kernel readiness, before it services its sockets.
// This makes immediate admission delivery independent of worker scheduling.
std::atomic<bool> pause_router{}, router_paused{};
void pause_after_poll(nfds_t count)
{
  if (count >= 2 && pause_router) {
    router_paused = true;
    while (pause_router) std::this_thread::yield();
    router_paused = false;
  }
}
extern "C" int poll(pollfd * descriptors, nfds_t count, int timeout)
{
  static const auto real_poll =
    reinterpret_cast<int (*)(pollfd *, nfds_t, int)>(::dlsym(RTLD_NEXT, "poll"));
  const auto result = real_poll(descriptors, count, timeout);
  pause_after_poll(count);
  return result;
}
// Release SDK builds fortify poll(vector.data(), vector.size(), ...) through
// __poll_chk; cover that entry point without weakening production hardening.
extern "C" int __poll_chk(pollfd * descriptors, nfds_t count, int timeout, std::size_t bytes)
{
  static const auto real_poll =
    reinterpret_cast<int (*)(pollfd *, nfds_t, int, std::size_t)>(::dlsym(RTLD_NEXT, "__poll_chk"));
  const auto result = real_poll(descriptors, count, timeout, bytes);
  pause_after_poll(count);
  return result;
}

void require(bool value, const char * message)
{
  if (!value) throw std::runtime_error(message);
}

bool wait(const std::function<bool()> & predicate)
{
  const auto end = std::chrono::steady_clock::now() + 2s;
  while (std::chrono::steady_clock::now() < end) {
    if (predicate()) return true;
    std::this_thread::sleep_for(1ms);
  }
  return false;
}
struct Client
{
  int fd;
  explicit Client(const std::string & path)
  {
    fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    auto address = wire::address(path);
    require(
      fd >= 0 && !::connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)),
      "runtime connection failed");
    timeval timeout{2, 0};
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  }
  ~Client()
  {
    if (fd >= 0) ::close(fd);
  }
  void send(const std::vector<uint8_t> & packet)
  {
    require(wire::send_all(fd, packet.data(), packet.size()), "runtime send failed");
  }
};

// Prepared for execution only after the parent's first actual KVM catch passes.
void m5ops_backpressure(const std::string & root)
{
  struct Backend
  {
    bool existed{std::getenv("CHIMAERA_DDS_BACKEND") != nullptr};
    std::string previous{existed ? std::getenv("CHIMAERA_DDS_BACKEND") : ""};
    Backend() { require(!::setenv("CHIMAERA_DDS_BACKEND", "m5ops", 1), "set backend failed"); }
    ~Backend()
    {
      if (existed)
        ::setenv("CHIMAERA_DDS_BACKEND", previous.c_str(), 1);
      else
        ::unsetenv("CHIMAERA_DDS_BACKEND");
    }
  } backend;
  constexpr auto frame_bytes = chimaera_dds::m5ops_max_frame_bytes;
  constexpr auto bulk_port = chimaera_dds::bulk_port_bit | 31U;
  constexpr auto control_port = 45U;
  auto frame = [](unsigned source, wire::Locator destination, std::size_t size, uint8_t tag) {
    std::vector<uint8_t> body(size - wire::header_bytes, tag);
    std::memcpy(body.data(), "RTPS", 4);
    return wire::packet(wire::data, wire::local(source), destination, body.data(), body.size());
  };
  {
    Router host(root + "/m5ops-outgoing", 1, 2 * frame_bytes);
    Client bulk(root + "/m5ops-outgoing"), control(root + "/m5ops-outgoing");
    auto packet = frame(1, wire::local(2, bulk_port), frame_bytes, 1);
    bulk.send(packet);
    packet.back() = 2;
    bulk.send(packet);
    require(wait([&] { return host.pending() == 2; }), "bulk remote budget not filled");
    // The third complete frame must park, rather than fail or enter the queue.
    packet.back() = 3;
    bulk.send(packet);
    control.send(frame(1, wire::local(2, control_port), 116, 4));
    require(wait([&] { return host.pending() == 3; }), "bulk pressure blocked control");
    host.check();
    std::vector<uint32_t> ports;
    std::weak_ptr<void> refused;
    const auto accepted = host.transfer_outgoing([&](chimaera_dds::Packet offered) {
      const auto port = wire::locator(offered.data() + 28).port;
      ports.push_back(port);
      if (port == bulk_port) refused = offered.owner();
      return port == control_port;
    });
    require(
      accepted == 1 && ports == std::vector<uint32_t>{control_port, bulk_port} &&
        host.pending() == 2 && !refused.expired(),
      "outgoing refusal changed credit/ownership or control priority");
    auto first = host.take();
    auto second = host.take();
    require(
      first && second && first->data()[first->size() - 1] == 1 &&
        second->data()[second->size() - 1] == 2,
      "bulk FIFO changed under backpressure");
    require(wait([&] { return host.pending() == 1; }), "parked frame did not recover credit");
    auto third = host.take();
    require(third && third->data()[third->size() - 1] == 3, "parked frame lost or changed");
    control.send(frame(1, wire::local(2, control_port), 116, 5));
    bulk.send(frame(1, wire::local(2, bulk_port), 116, 6));
    require(wait([&] { return host.pending() == 2; }), "independent-lane retry setup failed");
    require(
      host.transfer_outgoing([&](chimaera_dds::Packet offered) {
        return wire::locator(offered.data() + 28).port == bulk_port;
      }) == 1,
      "control refusal prevented the bulk lane from transferring");
    auto retained_control = host.take();
    require(
      retained_control && retained_control->data()[retained_control->size() - 1] == 5 &&
        host.pending() == 0,
      "refused control packet lost ownership or changed");
    host.check();
  }
  {
    Router host(root + "/m5ops-fanout", 1, 2 * frame_bytes);
    Client producer(root + "/m5ops-fanout"), fast(root + "/m5ops-fanout"),
      slow(root + "/m5ops-fanout"), control(root + "/m5ops-fanout");
    auto ready = [&](Client & client, unsigned port, const std::string & topic) {
      const auto local = wire::local(1, port);
      client.send(wire::packet(wire::open, local, local));
      std::vector<uint8_t> registration{0};
      registration.insert(registration.end(), topic.begin(), topic.end());
      client.send(
        wire::packet(wire::endpoint, local, local, registration.data(), registration.size()));
      require(wait([&] { return host.endpoint_ready(topic, false); }), "fanout not registered");
    };
    ready(fast, bulk_port, "/m5ops/fast");
    ready(slow, bulk_port, "/m5ops/slow");
    ready(control, control_port, "/m5ops/control");
    const auto outgoing = frame(1, wire::local(2, bulk_port), frame_bytes, 1);
    producer.send(outgoing);
    producer.send(outgoing);
    require(wait([&] { return host.pending() == 2; }), "remote budget not full before ingress");
    struct Resume
    {
      ~Resume() { pause_router = false; }
    } resume;
    pause_router = true;
    const std::string barrier_topic = "/m5ops/barrier";
    std::vector<uint8_t> barrier{0};
    barrier.insert(barrier.end(), barrier_topic.begin(), barrier_topic.end());
    producer.send(
      wire::packet(wire::endpoint, wire::local(1), wire::local(1), barrier.data(), barrier.size()));
    require(wait([] { return router_paused.load(); }), "fanout worker pause failed");
    auto broadcast = wire::local(1, bulk_port);
    broadcast.address[0] = 0;
    // Two 6 MiB recipients consume 12 of the independent 18 MiB local budget.
    // The next fanout can fit one recipient but must atomically refuse both.
    const auto incoming = frame(2, broadcast, 2 * frame_bytes / 3, 7);
    auto storage = std::make_shared<std::vector<uint8_t>>(incoming);
    require(
      host.try_receive(chimaera_dds::Packet::adopt(storage->data(), storage->size(), storage)),
      "remote output pressure consumed the independent local budget");
    auto refused_storage = std::make_shared<std::vector<uint8_t>>(incoming);
    std::weak_ptr<void> refused = refused_storage;
    auto candidate = chimaera_dds::Packet::adopt(
      refused_storage->data(), refused_storage->size(), refused_storage);
    require(!host.try_receive(candidate), "partial fanout accepted without full credit");
    candidate = {};
    refused_storage.reset();
    require(refused.expired(), "refused fanout retained ownership or partially enqueued");
    const auto small = frame(2, wire::local(1, control_port), 116, 8);
    auto small_storage = std::make_shared<std::vector<uint8_t>>(small);
    require(
      host.try_receive(
        chimaera_dds::Packet::adopt(small_storage->data(), small_storage->size(), small_storage)),
      "bulk local pressure consumed control credit");
    std::vector<uint8_t> received(small.size());
    require(
      wire::receive_all(control.fd, received.data(), received.size()) && received == small,
      "control immediate delivery failed under bulk pressure");
    require(host.pending() == 2, "local delivery changed remote output credit");
    host.check();
  }
}

int main()
{
  try {
    const auto root = (std::filesystem::temp_directory_path() /
                       ("chimaera-router-test-" + std::to_string(::getpid())))
                        .string();
    std::filesystem::create_directories(root);
    {
      Router host(root + "/host", 1), guest(root + "/guest", 2);
      Client sender(root + "/host"), receiver(root + "/guest");
      auto source = wire::local(1, 7), destination = wire::local(2, 7);
      sender.send(wire::packet(wire::open, source, source));
      receiver.send(wire::packet(wire::open, destination, destination));
      const std::string ready = "/receiver-ready";
      std::vector<uint8_t> ready_body{0};
      ready_body.insert(ready_body.end(), ready.begin(), ready.end());
      receiver.send(wire::packet(
        wire::endpoint, destination, destination, ready_body.data(), ready_body.size()));
      require(wait([&] { return guest.endpoint_ready(ready, false); }), "receiver not registered");
      std::vector<uint8_t> body(64, 0xa5);
      std::memcpy(body.data(), "RTPS", 4);
      auto packet = wire::packet(wire::data, source, destination, body.data(), body.size());
      sender.send(packet);
      require(wait([&] { return host.pending() == 1; }), "outgoing packet missing");
      pollfd before{receiver.fd, POLLIN, 0};
      require(::poll(&before, 1, 0) == 0, "packet bypassed the simulation boundary");
      auto transferred = host.take();
      require(
        transferred && transferred->size() == packet.size() &&
          !std::memcmp(transferred->data(), packet.data(), packet.size()),
        "outgoing packet changed");
      guest.receive(std::move(*transferred));
      pollfd after{receiver.fd, POLLIN, 0};
      require(::poll(&after, 1, 2000) == 1, "admitted packet not delivered");
      std::vector<uint8_t> received(packet.size());
      require(
        wire::receive_all(receiver.fd, received.data(), received.size()) && received == packet,
        "received bytes changed");
      bool rejected = false;
      try {
        host.receive(packet.data(), packet.size());
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      require(rejected, "wrong-side ingress accepted");
      packet[0] = 0;
      rejected = false;
      try {
        guest.receive(packet.data(), packet.size());
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      require(rejected, "malformed ingress accepted");
      const std::string topic = "/test";
      std::vector<uint8_t> registration{1};
      registration.insert(registration.end(), topic.begin(), topic.end());
      sender.send(
        wire::packet(wire::endpoint, source, source, registration.data(), registration.size()));
      require(wait([&] { return host.endpoint_ready(topic, true); }), "endpoint not registered");
      sender.send(
        wire::packet(wire::endpoint, source, source, registration.data(), registration.size()));
      registration[0] = 0x81;
      sender.send(
        wire::packet(wire::endpoint, source, source, registration.data(), registration.size()));
      sender.send(wire::packet(wire::data, source, destination, body.data(), body.size()));
      require(wait([&] { return host.pending() == 1; }), "endpoint count barrier missing");
      require(host.endpoint_ready(topic, true), "disposing one of two endpoints erased readiness");
      (void)host.take();
      sender.send(
        wire::packet(wire::endpoint, source, source, registration.data(), registration.size()));
      require(
        wait([&] { return !host.endpoint_ready(topic, true); }), "disposed endpoint retained");
      registration[0] = 1;
      sender.send(
        wire::packet(wire::endpoint, source, source, registration.data(), registration.size()));
      require(wait([&] { return host.endpoint_ready(topic, true); }), "endpoint not recreated");
      ::close(sender.fd);
      sender.fd = -1;
      require(wait([&] { return !host.endpoint_ready(topic, true); }), "stale endpoint retained");
    }
    {
      Router bounded(root + "/bounded", 1, 2 * chimaera_dds::max_packet_bytes);
      Client sender(root + "/bounded");
      std::vector<uint8_t> body(chimaera_dds::max_packet_bytes);
      std::memcpy(body.data(), "RTPS", 4);
      auto packet =
        wire::packet(wire::data, wire::local(1), wire::local(2), body.data(), body.size());
      sender.send(packet);
      require(wait([&] { return bounded.pending() == 1; }), "bounded queue missing first packet");
      sender.send(packet);
      require(
        wait([&] {
          try {
            bounded.check();
          } catch (const std::runtime_error &) {
            return true;
          }
          return false;
        }),
        "queue overflow not reported");
      bool rejected = false;
      try {
        (void)bounded.take();
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      require(rejected, "runtime failure did not persist");
    }
    {
      Router host(root + "/fragmented", 1);
      Client sender(root + "/fragmented");
      std::vector<uint8_t> body(921600, 0xa5);
      std::memcpy(body.data(), "RTPS", 4);
      const auto packet =
        wire::packet(wire::data, wire::local(1), wire::local(2), body.data(), body.size());
      // Deliberately split the header and payload; the next frame immediately
      // follows the first on the same byte stream.
      for (const auto [begin, end] :
           {std::pair{0u, 7u}, {7u, 51u}, {51u, 63u}, {63u, unsigned(packet.size())}}) {
        require(wire::send_all(sender.fd, packet.data() + begin, end - begin), "split send failed");
        std::this_thread::sleep_for(1ms);
      }
      sender.send(packet);
      require(wait([&] { return host.pending() == 2; }), "split/coalesced frames missing");
      for (unsigned i = 0; i < 2; ++i) {
        auto received = host.take();
        require(
          received && received->size() == packet.size() &&
            !std::memcmp(received->data(), packet.data(), packet.size()),
          "split/coalesced frame corrupted");
      }
    }
    {
      Router guest(root + "/loans", 2);
      Client fast(root + "/loans"), slow(root + "/loans");
      const auto local = wire::local(2, 7);
      for (auto * receiver : {&fast, &slow}) {
        receiver->send(wire::packet(wire::open, local, local));
        const std::string topic = receiver == &fast ? "/fast" : "/slow";
        std::vector<uint8_t> registration{0};
        registration.insert(registration.end(), topic.begin(), topic.end());
        receiver->send(
          wire::packet(wire::endpoint, local, local, registration.data(), registration.size()));
        require(wait([&] { return guest.endpoint_ready(topic, false); }), "fanout not ready");
      }
      std::vector<uint8_t> body(chimaera_dds::max_packet_bytes, 0x5a);
      std::memcpy(body.data(), "RTPS", 4);
      auto destination = local;
      destination.address[0] = 0;  // Broadcast to both local recipients.
      const auto expected =
        wire::packet(wire::data, wire::local(1), destination, body.data(), body.size());
      auto storage = std::make_shared<std::vector<uint8_t>>(expected);
      std::weak_ptr<void> lifetime = storage;
      guest.receive(chimaera_dds::Packet::adopt(storage->data(), storage->size(), storage));
      storage.reset();
      require(!lifetime.expired(), "queued loan released early");
      std::vector<uint8_t> received(expected.size());
      require(
        wire::receive_all(fast.fd, received.data(), received.size()) && received == expected,
        "loaned fanout corrupted");
      require(!lifetime.expired(), "slow recipient lost its loan");
      ::close(slow.fd);
      slow.fd = -1;
      require(wait([&] { return lifetime.expired(); }), "disconnected recipient retained loan");
      guest.check();
    }
    {
      Router guest(root + "/immediate", 2);
      Client receiver(root + "/immediate");
      const auto local = wire::local(2, 7);
      receiver.send(wire::packet(wire::open, local, local));
      const std::string topic = "/immediate";
      std::vector<uint8_t> registration{0};
      registration.insert(registration.end(), topic.begin(), topic.end());
      const auto barrier =
        wire::packet(wire::endpoint, local, local, registration.data(), registration.size());
      receiver.send(barrier);
      require(wait([&] { return guest.endpoint_ready(topic, false); }), "recipient not ready");
      struct Resume
      {
        ~Resume() { pause_router = false; }
      } resume;
      pause_router = true;
      receiver.send(barrier);
      require(wait([] { return router_paused.load(); }), "router pause failed");
      std::vector<uint8_t> body(64, 0xa5);
      std::memcpy(body.data(), "RTPS", 4);
      const auto packet = wire::packet(wire::data, wire::local(1), local, body.data(), body.size());
      auto second = packet;
      second.back() = 0x5a;
      guest.receive(packet.data(), packet.size());
      guest.receive(second.data(), second.size());
      std::vector<uint8_t> received(2 * packet.size());
      require(
        ::recv(receiver.fd, received.data(), received.size(), MSG_DONTWAIT | MSG_WAITALL) ==
          ssize_t(received.size()),
        "admitted packets waited for router worker");
      require(
        !std::memcmp(received.data(), packet.data(), packet.size()) &&
          !std::memcmp(received.data() + packet.size(), second.data(), second.size()),
        "immediate delivery changed packet bytes or order");
      // A failed fast-path write is retired by the worker, which still owns its
      // poll snapshot. It must release the packet and preserve router health.
      require(!::shutdown(receiver.fd, SHUT_RD), "receiver shutdown failed");
      auto storage = std::make_shared<std::vector<uint8_t>>(packet);
      std::weak_ptr<void> lifetime = storage;
      guest.receive(chimaera_dds::Packet::adopt(storage->data(), storage->size(), storage));
      storage.reset();
      require(!lifetime.expired(), "failed recipient queue was not retained for retirement");
      pause_router = false;
      require(wait([&] { return lifetime.expired(); }), "failed fast-path write retained loan");
      guest.check();
    }
    {
      int sockets[2];
      require(!::socketpair(AF_UNIX, SOCK_STREAM, 0, sockets), "socketpair failed");
      int capacity = 1024;
      ::setsockopt(sockets[0], SOL_SOCKET, SO_SNDBUF, &capacity, sizeof(capacity));
      timeval timeout{0, 10000};
      ::setsockopt(sockets[0], SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
      std::vector<uint8_t> body(chimaera_dds::max_packet_bytes, 0x3c);
      const auto prefix = wire::header(wire::data, wire::local(1), wire::local(2), body.size());
      std::vector<uint8_t> received(prefix.size() + body.size());
      bool complete = false;
      std::thread reader(
        [&] { complete = wire::receive_all(sockets[1], received.data(), received.size()); });
      const auto sent = wire::send_frame(sockets[0], prefix, body.data(), body.size());
      ::shutdown(sockets[0], SHUT_WR);
      reader.join();
      require(
        sent && complete && !std::memcmp(received.data(), prefix.data(), prefix.size()) &&
          !std::memcmp(received.data() + prefix.size(), body.data(), body.size()),
        "scatter/gather stream corrupted");
      const auto empty = wire::header(wire::open, wire::local(1), wire::local(1));
      require(wire::send_frame(sockets[1], empty, nullptr, 0), "empty frame send failed");
      require(
        wire::receive_all(sockets[0], received.data(), empty.size()) &&
          !std::memcmp(received.data(), empty.data(), empty.size()),
        "empty frame corrupted");
      ::close(sockets[0]);
      ::close(sockets[1]);
    }
    m5ops_backpressure(root);
    std::filesystem::remove(root);
    std::cout << "Router boundary, byte integrity, malformed ingress, endpoint lifecycle and "
                 "capacity; split frames, shared loans, fanout, immediate delivery and "
                 "scatter/gather passed\n";
    return 0;
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
