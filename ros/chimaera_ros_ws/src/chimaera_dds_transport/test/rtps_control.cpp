#include "rtps_control.hpp"

#include <chimaera_dds_transport/router.hpp>
#include <chrono>
#include <cstring>
#include <iostream>
#include <thread>

using chimaera_dds::Packet;
namespace wire = chimaera_dds::wire;

void require(bool condition, const char * message)
{
  if (!condition) throw std::runtime_error(message);
}
Packet frame(uint8_t kind, unsigned count, bool little = true, bool bitmap = false)
{
  std::vector<uint8_t> body(20 + 4 + (kind == 7 || bitmap ? 28 : 24));
  std::memcpy(body.data(), "RTPS", 4);
  body[20] = kind;
  body[21] = little;
  body[little ? 22 : 23] = uint8_t(body.size() - 24);
  body[body.size() - 1] = uint8_t(count);
  if (bitmap) body[24 + 16 + (little ? 0 : 3)] = 32;
  auto bytes =
    wire::packet(wire::data, wire::local(1), wire::local(2, 25160), body.data(), body.size());
  auto result = Packet::for_overwrite(bytes.size());
  std::memcpy(result.data(), bytes.data(), bytes.size());
  return result;
}
int main()
{
  try {
    for (bool little : {false, true}) {
      for (uint8_t kind : {6, 7}) {
        auto first = frame(kind, 1, little), next = frame(kind, 2, little);
        const auto key = chimaera_dds::repeated_control_key(first);
        require(
          !key.empty() && key == chimaera_dds::repeated_control_key(next),
          "equivalent control state did not share a key");
        require(
          key == chimaera_dds::pending_control_key(next),
          "credit handoff lost the discovery retry key");
        next.data()[wire::header_bytes + 24] = 1;
        require(key != chimaera_dds::repeated_control_key(next), "reader identity was ignored");
        next = frame(kind, 2, little);
        next.data()[wire::header_bytes + 21] |= 2;
        require(key != chimaera_dds::repeated_control_key(next), "control flags were ignored");
        next = frame(kind, 2, little);
        next.data()[wire::header_bytes + 32] = 1;
        require(key != chimaera_dds::repeated_control_key(next), "sequence range was ignored");
      }
    }
    auto ack = frame(6, 1, true, true), nack = frame(6, 2, true, true);
    nack.data()[wire::header_bytes + 24 + 20] = 1;
    require(
      chimaera_dds::repeated_control_key(ack) != chimaera_dds::repeated_control_key(nack),
      "changed acknowledgement bitmap was ignored");
    auto data = frame(0x15, 1);
    require(chimaera_dds::repeated_control_key(data).empty(), "DATA was replaceable");
    auto bad = frame(7, 1);
    bad.data()[wire::header_bytes + 22] = 255;
    require(chimaera_dds::repeated_control_key(bad).empty(), "truncated control was replaceable");
    auto application = frame(7, 1);
    wire::write32(application.data() + 28, chimaera_dds::application_port_bit | 42);
    require(
      chimaera_dds::repeated_control_key(application).empty(), "application QoS was replaced");

    std::vector<Packet> snapshot{frame(7, 1), frame(7, 2), frame(6, 1), data, application};
    std::vector<bool> keep(snapshot.size(), true);
    chimaera_dds::retain_control(snapshot, keep);
    require(
      keep == std::vector<bool>({false, true, true, true, true}),
      "received retries were not coalesced conservatively");
    keep.assign(1, true);
    chimaera_dds::retain_control(std::vector<Packet>{frame(7, 1)}, keep);
    require(keep[0], "received control state escaped its snapshot");

    ::setenv("CHIMAERA_DDS_BACKEND", "m5ops", 1);
    const auto path = "/tmp/chimaera-control-test-" + std::to_string(::getpid());
    chimaera_dds::Router router(path, 1);
    int socket = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    auto address = wire::address(path);
    require(
      socket >= 0 && !::connect(socket, reinterpret_cast<sockaddr *>(&address), sizeof(address)),
      "connect failed");
    auto send = [&](const Packet & packet) {
      require(wire::send_all(socket, packet.data(), packet.size()), "send failed");
    };
    send(frame(7, 1));
    send(data);
    for (unsigned count = 2; count <= 100; ++count) send(frame(7, count));
    // A same-socket registration confirms every preceding frame was queued.
    auto mark = [&](const std::string & topic) {
      const auto marker = std::string(1, '\1') + topic;
      const auto done =
        wire::packet(wire::endpoint, wire::local(1), wire::local(1), marker.data(), marker.size());
      require(wire::send_all(socket, done.data(), done.size()), "registration failed");
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
      while (!router.endpoint_ready(topic, true) && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      require(router.endpoint_ready(topic, true), "router did not finish input");
    };
    mark("/done");
    auto heartbeat = router.take();
    require(
      heartbeat && heartbeat->data()[heartbeat->size() - 1] == 100,
      "latest control count was not retained");
    // Refusal retains the queued DATA, then successful admission releases it.
    require(router.transfer_outgoing([](Packet) { return false; }) == 0, "refusal lost credit");
    auto received = router.take();
    require(
      received && received->size() == data.size() &&
        !std::memcmp(received->data(), data.data(), data.size()),
      "DATA changed or reordered");
    require(!router.take(), "duplicate heartbeats remained queued");
    send(frame(7, 101));
    send(frame(7, 102));
    mark("/again");
    require(
      router.transfer_outgoing([](Packet packet) {
        require(packet.data()[packet.size() - 1] == 102, "replacement after take failed");
        return true;
      }) == 1,
      "transfer did not release the repeated-control entry");
    send(frame(7, 103));
    mark("/last");
    require(router.take().has_value() && !router.take(), "replacement after transfer failed");
    ::close(socket);
    std::cout << "RTPS repeated-control tests passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
