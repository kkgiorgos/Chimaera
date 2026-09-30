#include "../src/controller_protocol.hpp"

#include <deque>
#include <iostream>

using namespace chimaera;
namespace {
void expect(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template <typename F> void rejects(F operation) {
    bool rejected = false;
    try { operation(); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected, "queue overflow was accepted");
}
Message frame(char route, unsigned value, std::size_t size = 3) {
    Message message(size);
    message[0] = static_cast<std::byte>(route);
    message[1] = static_cast<std::byte>((value >> 8) & 255);
    message[2] = static_cast<std::byte>(value & 255);
    return message;
}
struct Producer : DataProducer {
    std::deque<Message> pending;
    bool coalesce{true};
    std::optional<Message> take() override {
        if (pending.empty()) return std::nullopt;
        auto message = std::move(pending.front());
        pending.pop_front();
        return message;
    }
    std::string_view coalescing_key(const Message& message) const noexcept override {
        if (coalesce && !message.empty() && message[0] == std::byte{'c'}) return "clock";
        return {};
    }
};
struct Fifo : DataProducer {
    std::optional<Message> take() override { return std::nullopt; }
};
}

int main() {
    try {
        Producer producer;
        // Draining a burst larger than the packet limit must still retain its
        // newest clock, even when take() exposes updates one at a time.
        for (unsigned i = 0; i < 4096; ++i) {
            producer.pending.push_back(frame('c', i));
            if (i == 10 || i == 2000) producer.pending.push_back(frame('s', i));
        }
        const auto batch = control::collect(producer);
        expect(producer.pending.empty(), "clock burst not fully drained");
        expect(batch == control::Batch({frame('s', 10), frame('s', 2000), frame('c', 4095)}),
               "latest clock or scan FIFO order lost");
        const auto packet = control::decode(
            control::encode({control::Kind::reply, 7, Duration(10), batch}), control::Kind::reply);
        expect(packet.messages == batch, "coalescing changed wire round trip");

        // A guest can miss several host boundaries. Only its latest pending
        // clock survives, while every scan remains in order.
        control::Batch outgoing;
        for (unsigned boundary = 0; boundary < 100; ++boundary)
            control::append(outgoing, {frame('c', boundary), frame('s', boundary)}, producer);
        expect(outgoing.size() == 101, "clock backlog grew across boundaries");
        for (unsigned i = 0; i < 99; ++i)
            expect(outgoing[i] == frame('s', i), "scan order changed across boundaries");
        expect(outgoing[99] == frame('c', 99) && outgoing[100] == frame('s', 99),
               "newest clock position changed");

        // Superseding an existing clock is allowed at both capacity limits.
        outgoing.assign(control::max_messages - 1, frame('s', 0));
        outgoing.push_back(frame('c', 1));
        control::append(outgoing, {frame('c', 2)}, producer);
        expect(outgoing.size() == control::max_messages && outgoing.back() == frame('c', 2),
               "clock replacement failed at message capacity");
        rejects([&] { control::append(outgoing, {frame('s', 1)}, producer); });
        outgoing = {frame('c', 1, control::max_bytes)};
        control::append(outgoing, {frame('c', 2, control::max_bytes)}, producer);
        expect(outgoing.size() == 1 && outgoing[0][2] == std::byte{2},
               "clock replacement failed at byte capacity");
        rejects([&] { control::append(outgoing, {frame('s', 1)}, producer); });
        outgoing.clear();

        // Existing producers opt out by default, including identical payloads.
        Fifo fifo;
        control::append(outgoing, {frame('c', 1), frame('c', 1), frame('s', 2)}, fifo);
        expect(outgoing.size() == 3, "default FIFO producer lost messages");
        producer.coalesce = false;
        producer.pending = {frame('c', 1), frame('c', 2)};
        expect(control::collect(producer).size() == 2, "FIFO collection lost messages");
        std::cout << "Clock burst/backlog coalescing, scan FIFO, wire compatibility and queue limits passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
