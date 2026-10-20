#include "../src/dma_protocol.hpp"
#include <stdexcept>

using namespace chimaera;
void expect(bool value) { if (!value) throw std::runtime_error("DMA framing/lifetime regression"); }
template<class F> void rejects(F action) {
    bool failed = false;
    try { action(); } catch (const std::runtime_error&) { failed = true; }
    expect(failed);
}
int main() {
    for (unsigned count = 1; count <= 64; ++count) {
        control::OutgoingQueue outgoing;
        std::vector<Message> expected;
        for (unsigned i = 0; i < count; ++i) {
            expected.emplace_back(i * 7, static_cast<std::byte>(i));
            outgoing.submit(expected.back());
        }
        const auto layout = dma::layout(outgoing.entries());
        expect(layout.count == count);
        auto wire = Message::for_overwrite(layout.size());
        dma::encode(wire.data(), layout, outgoing.entries());
        const auto* start = wire.data();
        const auto messages = dma::decode(std::move(wire));
        expect(messages == expected);
        for (const auto& message : messages)
            expect(message.data() >= start && message.data() + message.size() <= start + layout.size());
    }
    control::OutgoingQueue outgoing;
    outgoing.submit(Message(16, std::byte{1}));
    outgoing.submit(Message(1843337, std::byte{2}));
    outgoing.submit(Message(16, std::byte{3}));
    expect(dma::layout(outgoing.entries()).count == 1);
    outgoing.take();
    expect(dma::layout(outgoing.entries()).count == 1); // Never concatenate images.

    control::OutgoingQueue small;
    small.submit(Message(63, std::byte{7}));
    small.submit({});
    small.submit(Message(64, std::byte{8}));
    const auto layout = dma::layout(small.entries());
    std::vector<std::byte> memory(layout.size());
    dma::encode(memory.data(), layout, small.entries());
    unsigned returned = 0;
    auto owner = std::shared_ptr<void>(memory.data(), [&](void*) { ++returned; });
    auto root = Message::adopt(memory.data(), memory.size(), owner);
    owner.reset();
    auto borrowed = dma::decode(std::move(root));
    expect(returned == 0 && borrowed.size() == 3 && borrowed[1].empty());
    auto last = std::move(borrowed.back());
    borrowed.clear();
    expect(returned == 0 && last == Message(64, std::byte{8}));
    last = {};
    expect(returned == 1);

    auto valid = Message(memory.begin(), memory.end());
    auto bad = valid;
    bad[0] ^= std::byte{1};
    rejects([&] { dma::decode(bad); });
    bad = valid; dma::put32(bad.data() + 4, 0);
    rejects([&] { dma::decode(bad); });
    bad = valid; dma::put32(bad.data() + 4, 65);
    rejects([&] { dma::decode(bad); });
    bad = valid; bad.pop_back();
    rejects([&] { dma::decode(bad); });
    bad = valid; dma::put32(bad.data() + 12, 1);
    rejects([&] { dma::decode(bad); });
    bad = valid; dma::put32(bad.data() + dma::header_bytes, 4097);
    rejects([&] { dma::decode(bad); });
    bad = valid; dma::put32(bad.data() + dma::header_bytes, 62);
    rejects([&] { dma::decode(bad); });
}
