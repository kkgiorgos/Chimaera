// Structural diagnostic using the production queues, mailbox and DDS router.
// Deliberately stalled streams establish ordering; elapsed times are not a
// performance measurement. This does not execute the simulated DMA device.
#include <chimaera_dds_transport/router.hpp>
#include <gem5/chimaera_mailbox.hh>

#include "dma_protocol.hpp"
#include "protocol.hpp"

#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <stdexcept>

namespace wire = chimaera_dds::wire;
using chimaera_dds::Router;
using namespace std::chrono_literals;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Directory {
    std::string path;
    Directory() {
        char pattern[] = "/tmp/chimaera-hol-XXXXXX";
        const auto directory = ::mkdtemp(pattern);
        require(directory, "cannot create isolated socket directory");
        path = directory;
    }
    ~Directory() { std::filesystem::remove_all(path); }
};

struct Client {
    int fd{-1};
    explicit Client(const std::string& path) {
        fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        require(fd >= 0, "cannot create client socket");
        timeval timeout{3, 0};
        require(!::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)),
                "cannot set send timeout");
        require(!::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)),
                "cannot set receive timeout");
        auto address = wire::address(path);
        require(!::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)),
                "cannot connect client");
    }
    ~Client() { if (fd >= 0) ::close(fd); }
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;
    void send(const std::vector<uint8_t>& packet) {
        require(wire::send_all(fd, packet.data(), packet.size()), "client send failed");
    }
    std::vector<uint8_t> receive() {
        std::array<uint8_t, wire::header_bytes> prefix{};
        require(wire::receive_all(fd, prefix.data(), prefix.size()), "receive header failed");
        std::vector<uint8_t> packet(wire::length(prefix.data(), prefix.size()));
        std::memcpy(packet.data(), prefix.data(), prefix.size());
        require(wire::receive_all(fd, packet.data() + prefix.size(), packet.size() - prefix.size()),
                "receive payload failed");
        return packet;
    }
};

struct Notification {
    std::mutex mutex;
    std::condition_variable ready;
    // Notification takes this same mutex: a producer cannot notify between the
    // predicate check and the condition-variable wait becoming armed.
    void signal() {
        std::lock_guard lock(mutex);
        ready.notify_all();
    }
    void wait(Router& router) {
        std::unique_lock lock(mutex);
        require(ready.wait_for(lock, 3s, [&] { return router.pending() != 0; }),
                "router did not produce a complete packet");
        router.check();
    }
};

std::vector<uint8_t> packet(unsigned side, unsigned port, std::size_t size, uint8_t marker) {
    std::vector<uint8_t> body(size, marker);
    require(size >= 20, "RTPS header too short");
    std::memcpy(body.data(), "RTPS", 4);
    return wire::packet(wire::data, wire::local(side), wire::local(3 - side, port),
                        body.data(), body.size());
}

void expect_packet(Router& router, Notification& notification, const std::vector<uint8_t>& expected) {
    notification.wait(router);
    auto actual = router.take();
    require(actual && actual->size() == expected.size() &&
            !std::memcmp(actual->data(), expected.data(), expected.size()),
            "router returned an unexpected packet");
}

void queues(const std::string& root) {
    chimaera::control::OutgoingQueue queue;
    queue.submit(chimaera::Message(1024 * 1024));
    queue.submit(chimaera::Message(64));
    queue.submit(chimaera::Message(64));
    const auto first = chimaera::dma::layout(queue.entries());
    require(first.count == 1 && first.payload == 1024 * 1024, "unexpected bulk batch layout");
    (void)queue.take();
    require(chimaera::dma::layout(queue.entries()).count == 2, "small batch did not follow bulk");

    chimaera_mailbox::Mailbox writer(root + "/mailbox", true, 2, 1024 * 1024);
    chimaera_mailbox::Mailbox reader(root + "/mailbox", false, 2, 1024 * 1024);
    std::vector<uint8_t> bulk(1024 * 1024, 0x42), small(64, 0x24);
    require(writer.write(bulk.data(), bulk.size()) && writer.write(small.data(), small.size()),
            "could not fill mailbox");
    chimaera_mailbox::Mailbox::View view{};
    require(reader.peek(view) && view.size == bulk.size(), "mailbox did not retain bulk head");
    reader.pop();
    require(reader.peek(view) && view.size == small.size(), "small mailbox entry missing");
    reader.pop();
}

void ingress(const std::string& root) {
    Notification notification; // Outlives router and its worker callback.
    Router router(root + "/ingress", 1);
    router.set_notify([&] { notification.signal(); });
    Client bulk_sender(root + "/ingress"), independent_sender(root + "/ingress");
    const auto bulk = packet(1, 7, 1024 * 1024, 0x42);
    const auto same_stream_small = packet(1, 8, 64, 0x24);
    const auto independent_small = packet(1, 9, 64, 0x18);
    constexpr std::size_t prefix = wire::header_bytes + 32;
    require(wire::send_all(bulk_sender.fd, bulk.data(), prefix), "partial bulk send failed");
    independent_sender.send(independent_small);
    expect_packet(router, notification, independent_small);
    require(router.pending() == 0, "incomplete bulk became visible");
    // Complete the stalled frame before sending another frame on that stream.
    require(wire::send_all(bulk_sender.fd, bulk.data() + prefix, bulk.size() - prefix),
            "bulk remainder send failed");
    bulk_sender.send(same_stream_small);
    expect_packet(router, notification, bulk);
    expect_packet(router, notification, same_stream_small);
}

void egress(const std::string& root) {
    Notification notification;
    Router router(root + "/egress", 2);
    router.set_notify([&] { notification.signal(); });
    Client shared_receiver(root + "/egress"), independent_receiver(root + "/egress");
    const auto local = wire::local(2);
    for (unsigned port : {7U, 8U})
        shared_receiver.send(wire::packet(wire::open, local, wire::local(2, port)));
    independent_receiver.send(wire::packet(wire::open, local, wire::local(2, 9)));
    // A remote data packet on each stream is an acknowledgement that its
    // preceding channel registrations have been consumed by the router.
    const auto barrier = packet(2, 10, 64, 0x77);
    shared_receiver.send(barrier);
    independent_receiver.send(barrier);
    expect_packet(router, notification, barrier);
    expect_packet(router, notification, barrier);

    const auto bulk = packet(1, 7, 1024 * 1024, 0x42);
    const auto same_stream_small = packet(1, 8, 64, 0x24);
    const auto independent_small = packet(1, 9, 64, 0x18);
    auto admit = [&](const auto& data) { router.receive(data.data(), data.size()); };
    admit(bulk);
    admit(same_stream_small);
    admit(independent_small);
    // Deliberately leave the first client unread until the second receives.
    require(independent_receiver.receive() == independent_small,
            "independent recipient blocked behind bulk recipient");
    require(shared_receiver.receive() == bulk, "shared stream lost bulk FIFO");
    require(shared_receiver.receive() == same_stream_small, "shared stream lost small FIFO");
}

int main() {
    try {
        Directory directory;
        queues(directory.path);
        ingress(directory.path);
        egress(directory.path);
        std::cout << R"({"kind":"structural_probe","simulated_dma_executed":false,"latency_measured":false,"bulk_body_bytes":1048576,"small_body_bytes":64,"controller_large_precedes_small":true,"mailbox_large_precedes_small":true,"router_ingress_independent_stream_progress":true,"router_ingress_same_stream_fifo":true,"router_egress_independent_recipient_progress":true,"router_egress_same_stream_fifo":true})" << '\n';
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
