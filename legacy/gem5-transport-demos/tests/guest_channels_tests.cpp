#include "../examples/guest_channels.hpp"
#include <sstream>
#include <stdexcept>
using namespace chimaera;
void expect(bool ok) { if (!ok) throw std::runtime_error("guest channels assertion failed"); }
Message bytes(std::string_view s) { auto b = std::as_bytes(std::span(s)); return {b.begin(), b.end()}; }
int main() {
    int input[2];
    if (pipe(input)) return 1;
    try {
        std::ostringstream output;
        controller_example::GuestChannels console(input[0], output);
        auto write = [&](std::string_view s) { expect(::write(input[1], s.data(), s.size()) == static_cast<ssize_t>(s.size())); };
        write("send 1 par"); expect(!console.take());
        const std::array channels{QueueChannel{1, 4096}, QueueChannel{2, 4096}};
        QueueSerializer peer(channels);
        expect(peer.try_enqueue(2, bytes("automatic"))); expect(peer.try_enqueue(1, {}));
        console.submit(peer.serialize());
        expect(output.str().find("[channel 2] Received 9 bytes: automatic") != std::string::npos);
        expect(output.str().find("[channel 1] Received 0 bytes:") != std::string::npos);
        write("tial\nsend 2\nsend 1 second\nsend 9 invalid\nquit\n");
        auto bundle = console.take(); expect(bundle.has_value());
        QueueDeserializer received(channels); expect(received.deserialize(*bundle));
        expect(received.try_dequeue(1) == bytes("partial"));
        expect(received.try_dequeue(1) == bytes("second"));
        expect(received.try_dequeue(2) == Message{});
        expect(output.str().find("Choose channel 1 or 2") != std::string::npos);
        expect(output.str().find("Quit from the host") != std::string::npos);
        // A full-size service snapshot must not block the single consumer thread.
        for (int i = 0; i < 4096; ++i) expect(peer.try_enqueue(1, {}));
        console.submit(peer.serialize());
        write("send 2 eof"); ::close(input[1]); input[1] = -1;
        bundle = console.take(); expect(bundle.has_value()); expect(received.deserialize(*bundle));
        expect(received.try_dequeue(2) == bytes("eof")); expect(!console.take());
        expect(peer.try_enqueue(2, bytes("after eof"))); console.submit(peer.serialize());
        expect(output.str().find("after eof") != std::string::npos);
        ::close(input[0]);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n'; ::close(input[0]); if (input[1] >= 0) ::close(input[1]); return 1;
    }
}
