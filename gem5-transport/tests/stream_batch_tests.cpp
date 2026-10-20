#include "../src/controller_protocol.hpp"
#include <cstring>
#include <deque>
#include <iostream>

using namespace chimaera;
namespace {
void expect(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template<typename F> void rejects(F operation) {
    bool rejected = false;
    try { operation(); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected, "malformed batch accepted");
}
struct Wire : Transport {
    std::deque<Message> frames;
    std::vector<const std::byte*> source_addresses;
    SendResult send(std::span<const std::byte> data) override {
        source_addresses.push_back(data.data());
        frames.emplace_back(data.begin(), data.end());
        return {};
    }
    ReceiveResult receive() override {
        if (frames.empty()) return {{}, TransportError::disconnected, "truncated stream"};
        auto data = std::move(frames.front());
        frames.pop_front();
        return {std::move(data), TransportError::none, {}};
    }
};
Message manifest(std::initializer_list<std::size_t> sizes) {
    Message result;
    control::put(result, sizes.size());
    for (auto size : sizes) control::put(result, size);
    return result;
}
}
int main() {
    try {
        Wire wire;
        control::Batch stereo{Message(640 * 480 * 3), Message(640 * 480 * 3), {}};
        for (std::size_t i = 0; i < stereo[0].size(); ++i) {
            stereo[0][i] = static_cast<std::byte>(i % 251);
            stereo[1][i] = static_cast<std::byte>((i * 17) % 251);
        }
        control::send_batch(wire, stereo);
        expect(wire.frames.size() == 4 && wire.frames[0].size() == 32, "bad stereo manifest");
        expect(wire.source_addresses[1] == stereo[0].data() &&
               wire.source_addresses[2] == stereo[1].data(), "send copied payload into a batch");
        const auto left = wire.frames[1].data(), right = wire.frames[2].data();
        auto received = control::receive_batch(wire);
        expect(received == stereo && wire.frames.empty(), "stereo payload corruption or empty frame loss");
        expect(received[0].data() == left && received[1].data() == right,
               "receive copied payload out of a batch");
        control::send_batch(wire, {});
        expect(control::receive_batch(wire).empty(), "empty batch failed");
        for (auto malformed : std::vector<Message>{{}, manifest({1}), manifest({0, 1})}) {
            wire.frames.clear();
            wire.frames.push_back(std::move(malformed));
            rejects([&] { control::receive_batch(wire); });
        }
        wire.frames = {manifest({2}), Message(1)};
        rejects([&] { control::receive_batch(wire); });
        wire.frames = {manifest({1, 1}), Message(1)};
        rejects([&] { control::receive_batch(wire); });
        // Reject counts and byte totals before allocating/receiving payloads.
        auto excessive = manifest({control::max_bytes, 1});
        wire.frames = {excessive, Message(1)};
        rejects([&] { control::receive_batch(wire); });
        expect(wire.frames.size() == 1, "invalid manifest consumed a payload");
        Message count;
        control::put(count, control::max_messages + 1);
        wire.frames = {count};
        rejects([&] { control::receive_batch(wire); });
        auto trailing = manifest({});
        trailing.push_back(std::byte{});
        wire.frames = {trailing};
        rejects([&] { control::receive_batch(wire); });
        wire.frames.clear();
        rejects([&] { control::send_batch(wire, control::Batch(control::max_messages + 1)); });
        expect(wire.frames.empty(), "invalid send emitted a partial batch");
        std::cout << "Owned stereo streaming, payload integrity and manifest failure paths passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
