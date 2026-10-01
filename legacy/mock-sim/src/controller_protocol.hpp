#pragma once

#include <mock_sim/controller.hpp>
#include <array>
#include <cstdint>
#include <stdexcept>
#include <utility>

namespace mock_sim::detail {

// Separate controller endpoints must not be used for raw transport messages.
constexpr std::uint64_t magic = 0x4d53494d43545232; // MSIMCTR2: process-paused boundary protocol
constexpr std::size_t max_batch = 1024;
constexpr std::size_t max_bytes = 64 * 1024 * 1024;
using Batch = std::vector<Message>;

inline void send(Transport& transport, std::span<const std::byte> bytes) {
    auto result = transport.send(bytes);
    if (!result.ok()) throw std::runtime_error(result.message);
}
inline Message receive(Transport& transport) {
    auto result = transport.receive();
    if (!result.ok()) throw std::runtime_error(result.message);
    return std::move(result.data);
}
inline void number(Transport& transport, std::uint64_t value) {
    std::array<std::byte, 8> bytes{};
    for (int i = 7; i >= 0; --i) { bytes[i] = std::byte(value & 255); value >>= 8; }
    send(transport, bytes);
}
inline std::uint64_t number(Transport& transport) {
    const auto bytes = receive(transport);
    if (bytes.size() != 8) throw std::runtime_error("invalid controller number");
    std::uint64_t value = 0;
    for (auto byte : bytes) value = (value << 8) | std::to_integer<unsigned>(byte);
    return value;
}
inline Batch collect(DataProducer& producer) {
    Batch batch;
    std::size_t bytes = 0;
    for (std::size_t i = 0; i < max_batch; ++i) {
        auto message = producer.take();
        if (!message) break;
        bytes += message->size();
        if (bytes > max_bytes) throw std::runtime_error("producer batch exceeds 64 MiB");
        batch.push_back(std::move(*message));
    }
    return batch;
}
inline void send_batch(Transport& transport, const Batch& batch) {
    number(transport, batch.size());
    for (const auto& message : batch) send(transport, message);
}
inline Batch receive_batch(Transport& transport) {
    const auto count = number(transport);
    if (count > max_batch) throw std::runtime_error("invalid controller batch size");
    Batch batch;
    std::size_t bytes = 0;
    for (std::uint64_t i = 0; i < count; ++i) {
        auto message = receive(transport);
        bytes += message.size();
        if (bytes > max_bytes) throw std::runtime_error("peer batch exceeds 64 MiB");
        batch.push_back(std::move(message));
    }
    return batch;
}
inline void deliver(Batch batch, DataConsumer& consumer) {
    for (auto& message : batch) consumer.submit(std::move(message));
}
inline bool valid(Duration interval, Duration poll) {
    return interval.count() > 0 && poll.count() > 0 && poll <= interval &&
           interval <= std::chrono::hours(1) &&
           1 + (interval.count() - 1) / poll.count() <= 100000;
}

} // namespace mock_sim::detail
