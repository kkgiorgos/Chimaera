#include <chimaera/dma_controller.hpp>
#include <gem5/chimaera_mailbox.hh>
#include "../src/dma_protocol.hpp"
#include <cstring>
#include <filesystem>
#include <future>
#include <source_location>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace chimaera;
void expect(bool value, std::source_location at = std::source_location::current()) {
    if (!value) throw std::runtime_error("DMA boundary regression at " + std::to_string(at.line()));
}
int main() {
    char directory[] = "/tmp/chimaera-dma-host-XXXXXX";
    expect(::mkdtemp(directory));
    const std::string base(directory), endpoint = base + "/time";
    const auto listener = ::socket(AF_UNIX, SOCK_STREAM, 0);
    expect(listener >= 0);
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strcpy(address.sun_path, endpoint.c_str());
    expect(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    expect(::listen(listener, 4) == 0);
    {
        Gem5TimingController timing(endpoint, std::chrono::seconds(2));
        DmaHostController host(timing, base + "/h2g", base + "/g2h");
        chimaera_mailbox::Mailbox input(base + "/h2g", false, 16);
        chimaera_mailbox::Mailbox output(base + "/g2h", false, 16);
        // Fill the input ring at the first boundary. The pending newest-value
        // entry remains in the coalescing queue until it can be committed.
        for (unsigned i = 0; i < 16; ++i) host.submit(Message(8192, static_cast<std::byte>(i)));
        host.submit(Message(8192, std::byte{40}), "state");
        auto server = std::async(std::launch::async, [&] {
            std::uint64_t tick = 0;
            unsigned steps = 0, ingress = 0, received = 0;
            while (true) {
                const auto fd = ::accept(listener, nullptr, nullptr);
                expect(fd >= 0);
                std::string command;
                char byte;
                while (command.empty() || command.back() != '\n') {
                    expect(::recv(fd, &byte, 1, 0) == 1);
                    command += byte;
                }
                std::string response;
                if (command == "CHIMAERA_INGRESS\n") {
                    expect(ingress++ == steps);
                    response = "OK INGRESS\n";
                } else if (command.starts_with("STEP_TICKS ")) {
                    expect(ingress == steps + 1);
                    chimaera_mailbox::Mailbox::View view{};
                    while (input.peek(view)) {
                        if (steps == 0 && received == 0) {
                            expect(view.size == 8 && !std::memcmp(view.data, "CHDMA002", 8));
                        } else {
                            const auto expected = received <= 16 ? received - 1 : 41;
                            Message wire(reinterpret_cast<const std::byte*>(view.data),
                                         reinterpret_cast<const std::byte*>(view.data) + view.size);
                            auto messages = dma::decode(std::move(wire));
                            expect(messages.size() == 1 && messages[0] == Message(8192, static_cast<std::byte>(expected)));
                        }
                        ++received;
                        input.pop();
                    }
                    expect(received == (steps == 0 ? 16 : 18));
                    Message bytes(1843337, static_cast<std::byte>(steps + 1));
                    Message wire(bytes.size() + dma::header_bytes);
                    dma::header(wire.data(), {1, bytes.size(), 0});
                    std::memcpy(wire.data() + dma::header_bytes, bytes.data(), bytes.size());
                    expect(output.write(wire.data(), wire.size()));
                    const auto ticks = std::stoull(command.substr(11));
                    response = "OK " + std::to_string(tick) + " " + std::to_string(tick + ticks) + "\n";
                    tick += ticks;
                    ++steps;
                } else {
                    expect(command == "QUIT\n" && steps == 2);
                    response = "BYE\n";
                }
                expect(::send(fd, response.data(), response.size(), MSG_NOSIGNAL) == static_cast<ssize_t>(response.size()));
                ::close(fd);
                if (response == "BYE\n") break;
            }
        });
        expect(!host.take());
        expect(!host.step(Duration{}).ok());
        expect(host.step(std::chrono::milliseconds(2)).ok());
        auto first = host.take();
        expect(first && *first == Message(1843337, std::byte{1}));
        // Several seconds of 1 kHz feedback while the consumer is stalled must
        // retain one newest state, rather than hit the 1024-message limit.
        for (unsigned i = 0; i < 4096; ++i)
            host.submit(Message(8192, std::byte{41}), "state");
        expect(host.step(std::chrono::milliseconds(2)).ok());
        auto second = host.take();
        expect(second && *second == Message(1843337, std::byte{2}));
        // Returning the mailbox slot never mutates an already returned Message.
        expect(*first == Message(1843337, std::byte{1}) && !host.take());
        expect(host.stop().state == ControllerState::stopped);
        expect(host.stop().state == ControllerState::stopped);
        server.get();
    }
    expect(!std::filesystem::exists(base + "/h2g") && !std::filesystem::exists(base + "/g2h"));
    {
        Gem5TimingController timing(endpoint);
        DmaHostController host(timing, base + "/h2g", base + "/g2h", 2048);
        for (unsigned i = 0; i < 2048; ++i) host.submit(Message(64));
        bool full = false;
        try { host.submit(Message(64)); } catch (const std::runtime_error&) { full = true; }
        expect(full);
    }
    ::close(listener);
    std::filesystem::remove_all(base);
}
