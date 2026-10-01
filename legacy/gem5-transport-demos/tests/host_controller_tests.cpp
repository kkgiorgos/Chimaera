#include <chimaera/gem5_controller.hpp>
#include "../src/controller_protocol.hpp"
#include "../src/framing.hpp"
#include <cstring>
#include <future>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace chimaera;
namespace {
void expect(bool condition) {
    if (!condition) throw std::runtime_error("host controller assertion failed");
}
struct Socket {
    int fd;
    explicit Socket(int value) : fd(value) { expect(fd >= 0); }
    ~Socket() { ::close(fd); }
};
sockaddr_un address(const std::string& path) {
    sockaddr_un result{};
    result.sun_family = AF_UNIX;
    expect(path.size() < sizeof(result.sun_path));
    std::memcpy(result.sun_path, path.c_str(), path.size() + 1);
    return result;
}
void transfer(const std::string& path, std::span<std::byte> bytes, bool sending) {
    Socket socket(::socket(AF_UNIX, SOCK_STREAM, 0));
    const auto addr = address(path);
    expect(::connect(socket.fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) == 0);
    if (sending) {
        const std::uint64_t size = bytes.size(); // gem5 uses a native host envelope
        expect(::send(socket.fd, &size, sizeof(size), MSG_NOSIGNAL) == sizeof(size));
    }
    while (!bytes.empty()) {
        const auto n = sending ? ::send(socket.fd, bytes.data(), bytes.size(), MSG_NOSIGNAL)
                               : ::recv(socket.fd, bytes.data(), bytes.size(), 0);
        expect(n > 0);
        bytes = bytes.subspan(n);
    }
}
control::Packet exchange(const std::string& g2h, const std::string& h2g,
                         control::Batch messages = {}) {
    auto data = control::encode({control::Kind::poll, 0, Duration{}, std::move(messages)});
    auto header = detail::encode(data.size());
    transfer(g2h, header, true);
    transfer(g2h, data, true);
    transfer(h2g, header, false);
    data.resize(detail::decode(header));
    transfer(h2g, data, false);
    return control::decode(data, control::Kind::reply);
}
}
int main() {
    char directory[] = "/tmp/chimaera-host-test-XXXXXX";
    expect(::mkdtemp(directory));
    const std::string base(directory), endpoint = base + "/time", g2h = base + "/g2h", h2g = base + "/h2g";
    try {
        Socket listener(::socket(AF_UNIX, SOCK_STREAM, 0));
        const auto addr = address(endpoint);
        expect(::bind(listener.fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) == 0);
        expect(::listen(listener.fd, 4) == 0);
        Gem5TimingController timing(endpoint, std::chrono::seconds(2));
        Gem5HostController host(timing, g2h, h2g);
        const Message first{std::byte{1}}, second{std::byte{2}};
        expect(!host.take());
        host.submit(first, "state");
        host.submit(second, "state");
        host.submit({});
        // Queuing data before step must not leak it into epoch-zero replies.
        const auto bootstrap = exchange(g2h, h2g);
        expect(bootstrap.epoch == 0 && bootstrap.messages.empty());
        auto server = std::async(std::launch::async, [&] {
            std::uint64_t tick = 0;
            for (int step = 0; step < 3; ++step) {
                Socket client(::accept(listener.fd, nullptr, nullptr));
                std::string command;
                char byte;
                do {
                    expect(::recv(client.fd, &byte, 1, 0) == 1);
                    command += byte;
                } while (byte != '\n');
                if (step == 2) {
                    expect(command == "QUIT\n");
                    expect(::send(client.fd, "BYE\n", 4, MSG_NOSIGNAL) == 4);
                    break;
                }
                expect(command.starts_with("STEP_TICKS "));
                // Fully decoded messages accumulate until the pause response.
                const auto reply = exchange(g2h, h2g, step == 0 ? control::Batch{first, {}}
                                                                          : control::Batch{second});
                expect(reply.epoch == static_cast<unsigned>(step + 1));
                expect(reply.messages == (step == 0 ? control::Batch{second, {}} : control::Batch{first}));
                // Another poll in the same interval must not resend detached data.
                const auto next_reply = exchange(g2h, h2g);
                expect(next_reply.epoch == reply.epoch && next_reply.messages.empty());
                const auto ticks = std::stoull(command.substr(11));
                const auto response = "OK " + std::to_string(tick) + " " + std::to_string(tick + ticks) + "\n";
                tick += ticks;
                expect(::send(client.fd, response.data(), response.size(), MSG_NOSIGNAL) ==
                       static_cast<ssize_t>(response.size()));
            }
        });
        expect(host.step(Duration(10), Duration(1)).ok());
        // The shared outgoing queue accepts fresh data after a detached drain.
        host.submit(first, "state");
        // Unconsumed data survives the next step and retains order.
        expect(host.step(Duration(10), Duration(1)).ok());
        expect(host.take() == first);
        expect(host.take() == Message{});
        expect(host.take() == second);
        expect(!host.take());
        expect(host.stop().state == ControllerState::stopped);
        expect(host.step(Duration(10), Duration(1)).state == ControllerState::stopped);
        bool rejected = false;
        try { host.submit(first); } catch (const std::runtime_error&) { rejected = true; }
        expect(rejected);
        server.get();
    } catch (...) {
        ::unlink(endpoint.c_str());
        ::rmdir(directory);
        throw;
    }
    ::unlink(endpoint.c_str());
    ::rmdir(directory);
}
