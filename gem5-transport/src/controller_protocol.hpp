#pragma once

#include <chimaera/controller.hpp>
#include <cstdint>
#include <stdexcept>
#include <utility>

namespace chimaera::control {
using Batch = std::vector<Message>;
inline constexpr std::size_t max_messages = 1024;
inline constexpr std::size_t max_bytes = 32 * 1024 * 1024;
inline constexpr std::uint64_t magic = 0x4348494d43545231; // CHIMCTR1
enum class Kind : std::uint64_t { poll = 1, reply = 2 };
struct Packet {
    Kind kind;
    std::uint64_t epoch{};
    Duration poll{};
    Batch messages{};
};

inline bool valid(Duration interval, Duration poll) {
    return interval.count() > 0 && poll.count() > 0 && poll <= interval &&
           interval <= std::chrono::hours(1) &&
           1 + (interval.count() - 1) / poll.count() <= 100000;
}
inline std::size_t bytes(const Batch& batch) {
    std::size_t count = 0;
    for (const auto& message : batch) {
        if (message.size() > max_bytes - count)
            throw std::runtime_error("controller queue exceeds 32 MiB");
        count += message.size();
    }
    if (batch.size() > max_messages)
        throw std::runtime_error("controller queue exceeds 1024 messages");
    return count;
}
inline void append(Batch& target, Batch source) {
    if (target.size() + source.size() > max_messages || bytes(target) + bytes(source) > max_bytes)
        throw std::runtime_error("controller queue limit exceeded");
    for (auto& message : source) target.push_back(std::move(message));
}
inline Batch collect(DataProducer& producer) {
    Batch batch;
    std::size_t count = 0;
    while (batch.size() < max_messages) {
        auto message = producer.take();
        if (!message) break;
        if (message->size() > max_bytes - count)
            throw std::runtime_error("producer batch exceeds 32 MiB");
        count += message->size();
        batch.push_back(std::move(*message));
    }
    return batch;
}
inline void put(Message& data, std::uint64_t value) {
    for (int shift = 56; shift >= 0; shift -= 8)
        data.push_back(static_cast<std::byte>((value >> shift) & 255));
}
inline Message encode(const Packet& packet) {
    Message data;
    data.reserve(40 + packet.messages.size() * 8 + bytes(packet.messages));
    put(data, magic);
    put(data, static_cast<std::uint64_t>(packet.kind));
    put(data, packet.epoch);
    put(data, packet.poll.count());
    put(data, packet.messages.size());
    for (const auto& message : packet.messages) {
        put(data, message.size());
        data.insert(data.end(), message.begin(), message.end());
    }
    return data;
}
inline Packet decode(std::span<const std::byte> data, Kind expected) {
    auto get = [&]() {
        if (data.size() < 8) throw std::runtime_error("truncated controller packet");
        std::uint64_t value = 0;
        for (auto byte : data.first(8)) value = (value << 8) | std::to_integer<unsigned>(byte);
        data = data.subspan(8);
        return value;
    };
    if (get() != magic || get() != static_cast<std::uint64_t>(expected))
        throw std::runtime_error("controller protocol mismatch");
    Packet packet{expected};
    packet.epoch = get();
    const auto poll = get();
    if (poll > static_cast<std::uint64_t>(Duration(std::chrono::hours(1)).count()))
        throw std::runtime_error("invalid controller poll duration");
    packet.poll = Duration(poll);
    const auto count = get();
    if (count > max_messages) throw std::runtime_error("invalid controller batch size");
    for (std::uint64_t i = 0; i < count; ++i) {
        const auto size = get();
        if (size > data.size()) throw std::runtime_error("truncated controller message");
        packet.messages.emplace_back(data.begin(), data.begin() + size);
        data = data.subspan(size);
    }
    bytes(packet.messages);
    if (!data.empty()) throw std::runtime_error("trailing controller data");
    return packet;
}
inline void send(Transport& transport, const Packet& packet) {
    const auto data = encode(packet);
    auto result = transport.send(data);
    if (!result.ok()) throw std::runtime_error(result.message);
}
inline Packet receive(Transport& transport, Kind expected) {
    auto result = transport.receive();
    if (!result.ok()) throw std::runtime_error(result.message);
    return decode(result.data, expected);
}
} // namespace chimaera::control
