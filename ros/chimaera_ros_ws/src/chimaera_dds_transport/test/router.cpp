#include <poll.h>

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
      require(transferred && *transferred == packet, "outgoing packet changed");
      guest.receive(transferred->data(), transferred->size());
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
    std::filesystem::remove(root);
    std::cout << "Router boundary, byte integrity, malformed ingress, endpoint lifecycle and "
                 "capacity passed\n";
    return 0;
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
