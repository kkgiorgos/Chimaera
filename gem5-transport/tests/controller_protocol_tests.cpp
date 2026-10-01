#include "../src/controller_protocol.hpp"
#include <iostream>
using namespace chimaera;
namespace {
void expect(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template <typename F> void rejects(F operation) {
    bool rejected = false;
    try { operation(); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected, "invalid input was accepted");
}
Message frame(char route, unsigned value, std::size_t size = 3) {
    Message message(size);
    message[0] = static_cast<std::byte>(route);
    message[1] = static_cast<std::byte>((value >> 8) & 255);
    message[2] = static_cast<std::byte>(value & 255);
    return message;
}
}
int main() {
    try {
        control::OutgoingQueue queue;
        for (unsigned i = 0; i < 4096; ++i) {
            queue.submit(frame('c', i), "clock");
            if (i == 10 || i == 2000) queue.submit(frame('s', i));
        }
        const auto batch = queue.drain();
        expect(batch == control::Batch({frame('s', 10), frame('s', 2000), frame('c', 4095)}),
               "latest clock or scan FIFO order lost");
        const auto packet = control::encode(batch);
        expect(packet.size() == 8 + 8 * batch.size() + control::bytes(batch), "batch contains metadata");
        expect(control::decode(packet) == batch, "coalescing changed wire round trip");
        expect(control::decode(control::encode({})).empty(), "empty batch round trip failed");
        expect(control::decode(control::encode({{}})) == control::Batch{{}}, "empty message round trip failed");
        expect(control::startup_request().size() == 8, "startup request size changed");
        expect(control::decode_startup(control::encode_startup(Duration{})) == Duration{}, "pending startup failed");
        expect(control::decode_startup(control::encode_startup(Duration(3))) == Duration(3), "startup duration lost");
        auto wrong_version = control::encode_startup(Duration(3));
        wrong_version[7] = std::byte{'1'};
        rejects([&] { control::decode_startup(wrong_version); });
        rejects([&] { control::decode_startup(control::startup_request()); });
        rejects([&] { control::decode_startup(control::encode_startup(Duration(-1))); });
        rejects([&] { control::decode_startup(control::encode_startup(std::chrono::hours(1) + Duration(1))); });
        auto trailing_startup = control::encode_startup(Duration(3));
        trailing_startup.push_back(std::byte{});
        rejects([&] { control::decode_startup(trailing_startup); });
        rejects([&] { control::decode(control::startup_request()); });
        rejects([&] { control::decode({}); });
        auto truncated = packet;
        truncated.pop_back();
        rejects([&] { control::decode(truncated); });
        auto trailing = packet;
        trailing.push_back(std::byte{});
        rejects([&] { control::decode(trailing); });
        Message excessive_count;
        control::put(excessive_count, control::max_messages + 1);
        rejects([&] { control::decode(excessive_count); });
        Message excessive_payload;
        control::put(excessive_payload, 1);
        control::put(excessive_payload, control::max_bytes + 1);
        excessive_payload.resize(16 + control::max_bytes + 1);
        rejects([&] { control::decode(excessive_payload); });
        expect(queue.drain().empty(), "drain did not clear queue");
        for (unsigned i = 0; i < 100; ++i) {
            queue.submit(frame('c', i), "clock");
            queue.submit(frame('s', i));
        }
        auto outgoing = queue.drain();
        expect(outgoing.size() == 101, "clock backlog grew across boundaries");
        for (unsigned i = 0; i < 99; ++i)
            expect(outgoing[i] == frame('s', i), "scan order changed across boundaries");
        expect(outgoing[99] == frame('c', 99) && outgoing[100] == frame('s', 99),
               "newest clock position changed");
        for (unsigned i = 0; i < control::max_messages - 1; ++i) queue.submit(frame('s', 0));
        queue.submit(frame('c', 1), "clock");
        queue.submit(frame('c', 2), "clock");
        rejects([&] { queue.submit(frame('s', 1)); });
        // A rejected oversized replacement must retain the accepted clock.
        rejects([&] { queue.submit(frame('c', 3, control::max_bytes + 1), "clock"); });
        outgoing = queue.drain();
        expect(outgoing.size() == control::max_messages && outgoing.back() == frame('c', 2),
               "replacement or rejection changed queue at message capacity");
        queue.submit(frame('c', 1, control::max_bytes), "clock");
        queue.submit(frame('c', 2, control::max_bytes), "clock");
        rejects([&] { queue.submit(frame('s', 1)); });
        outgoing = queue.drain();
        expect(outgoing.size() == 1 && outgoing[0][2] == std::byte{2},
               "clock replacement failed at byte capacity");
        std::string key = "clock";
        queue.submit(frame('c', 1), key);
        key = "changed";
        queue.submit(frame('c', 2), "clock");
        queue.submit(frame('c', 2));
        queue.submit({});
        outgoing = queue.drain();
        expect(outgoing == control::Batch({frame('c', 2), frame('c', 2), {}}),
               "copied key, default FIFO or empty message lost");
        control::ReceivedQueue received;
        received.append(std::move(outgoing));
        expect(received.take() == frame('c', 2), "take lost FIFO order");
        expect(received.take() == frame('c', 2), "take lost duplicate");
        expect(received.take() == Message{}, "take lost empty message");
        expect(!received.take(), "empty queue returned a message");
        // Taking and appending at partial capacity must retain FIFO and buffers.
        control::Batch full;
        for (unsigned i = 0; i < control::max_messages; ++i) full.push_back(frame('s', i));
        const auto* payload = full[1].data();
        received.append(std::move(full));
        rejects([&] { received.append({frame('s', 1024)}); });
        expect(received.take() == frame('s', 0), "overflow changed received queue");
        received.append({frame('s', 1024)});
        auto second = received.take();
        expect(second && second->data() == payload, "receive queue copied payload storage");
        for (unsigned i = 2; i <= 1024; ++i)
            expect(received.take() == frame('s', i), "partial drain/append changed FIFO");
        expect(!received.take(), "partial drain left stale entries");
        received.append({frame('c', 1, control::max_bytes)});
        rejects([&] { received.append({frame('s', 2)}); });
        expect(received.take()->size() == control::max_bytes, "byte overflow changed queue");
        received.append({{}});
        expect(received.take() == Message{}, "byte count did not reset after take");
        received.append({frame('s', 1), frame('s', 2), {}});
        received.take();
        expect(received.drain() == control::Batch({frame('s', 2), {}}),
               "drain returned consumed messages or changed FIFO");
        expect(!received.take() && received.drain().empty(), "drain left stale entries");
        received.append({frame('c', 1, control::max_bytes)});
        received.drain();
        received.append({frame('c', 2, control::max_bytes)});
        expect(received.take()->size() == control::max_bytes, "drain did not reset byte accounting");

        // Both sides of a queue swap retain independent coalescing/accounting.
        control::OutgoingQueue detached;
        queue.submit(frame('c', 1), "clock");
        queue.swap(detached);
        queue.submit(frame('c', 2), "clock");
        expect(detached.drain() == control::Batch{frame('c', 1)}, "swap lost detached update");
        queue.swap(detached);
        expect(detached.drain() == control::Batch{frame('c', 2)}, "swap lost new pending update");
        expect(queue.drain().empty(), "swap left messages in pending queue");
        std::cout << "Batch framing, startup, coalescing and queue limits passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
